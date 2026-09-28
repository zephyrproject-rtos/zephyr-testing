# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Self-contained HTML report of API compatibility findings.

A full tree comparison can produce many thousands of findings, which is more
than a terminal is useful for. The page groups findings by file, collapses each
group, and filters client side so that a reviewer can narrow to, say, only the
silent behaviour changes in stable APIs.

The output is a single file with no external references: no CDN, no fonts, no
scripts. It can be attached to a CI job or opened straight from disk.
"""

from __future__ import annotations

import collections
import html
from dataclasses import dataclass

from .apidoc import Lifecycle
from .findings import Finding, Severity, sort_findings

#: Marker that findings.py puts in front of a change that still compiles.
SILENT_MARKER = "silently changes behavior:"

#: Status colours are reserved and always paired with a text label, never
#: carrying meaning by colour alone.
_SEVERITY_STYLE = {
    Severity.ERROR: ("critical", "Error"),
    Severity.WARNING: ("warning", "Warning"),
    Severity.NOTE: ("note", "Note"),
}

_LIFECYCLE_ORDER = [
    Lifecycle.STABLE,
    Lifecycle.UNVERSIONED,
    Lifecycle.UNSTABLE,
    Lifecycle.EXPERIMENTAL,
]

_CSS = """
:root {
  color-scheme: light;
  --surface: #fcfcfb;
  --plane: #f9f9f7;
  --border: #dedcd5;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --muted: #898781;
  --critical: #d03b3b;
  --warning: #fab219;
  --serious: #ec835a;
  --accent: #2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface: #1a1a19;
    --plane: #0d0d0d;
    --border: #34332f;
    --ink: #ffffff;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --critical: #e05a5a;
    --warning: #fab219;
    --serious: #ec835a;
    --accent: #3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19;
  --plane: #0d0d0d;
  --border: #34332f;
  --ink: #ffffff;
  --ink-2: #c3c2b7;
  --critical: #e05a5a;
  --accent: #3987e5;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 2rem 1.5rem 4rem;
  background: var(--plane);
  color: var(--ink);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .35rem; letter-spacing: -.01em; }
.sub { color: var(--ink-2); margin: 0 0 .25rem; }
.sub code { color: var(--ink); }
.meta { color: var(--muted); font-size: .85rem; margin: 0 0 1.5rem; }
code, pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

.tiles { display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: 1.25rem; }
.tile {
  flex: 1 1 8.5rem; background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: .8rem .9rem;
}
.tile .n { font-size: 1.7rem; font-weight: 600; letter-spacing: -.02em; }
.tile .k { color: var(--ink-2); font-size: .8rem; text-transform: uppercase;
           letter-spacing: .04em; }
.tile.is-critical .n { color: var(--critical); }
.tile.is-warning  .n { color: var(--warning); }
.tile.is-serious  .n { color: var(--serious); }

.panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: .9rem 1rem; margin-bottom: 1.25rem;
}
.row { display: flex; flex-wrap: wrap; gap: .5rem .9rem; align-items: center; }
.row + .row { margin-top: .6rem; padding-top: .6rem; border-top: 1px solid var(--border); }
.row > .lbl { color: var(--muted); font-size: .78rem; text-transform: uppercase;
              letter-spacing: .04em; min-width: 5.5rem; }
label.chk { display: inline-flex; align-items: center; gap: .35rem; cursor: pointer;
            font-size: .88rem; }
input[type="search"] {
  flex: 1 1 14rem; min-width: 10rem; padding: .45rem .6rem; border-radius: 7px;
  border: 1px solid var(--border); background: var(--plane); color: var(--ink);
  font-size: .9rem;
}
button {
  padding: .4rem .7rem; border-radius: 7px; border: 1px solid var(--border);
  background: var(--plane); color: var(--ink); font-size: .85rem; cursor: pointer;
}
button:hover { border-color: var(--muted); }

.badge {
  display: inline-flex; align-items: center; gap: .3rem; border-radius: 999px;
  padding: .1rem .5rem; font-size: .74rem; font-weight: 600; white-space: nowrap;
  border: 1px solid var(--border); color: var(--ink-2); background: var(--plane);
}
.badge .dot { width: .5rem; height: .5rem; border-radius: 50%; background: currentColor; }
.badge.sev-critical {
  color: var(--critical);
  border-color: color-mix(in srgb, var(--critical) 45%, transparent);
}
.badge.sev-warning {
  color: color-mix(in srgb, var(--warning) 72%, var(--ink));
  border-color: color-mix(in srgb, var(--warning) 55%, transparent);
}
.badge.sev-note { color: var(--ink-2); }
.badge.silent {
  color: var(--serious);
  border-color: color-mix(in srgb, var(--serious) 50%, transparent);
}

details.file {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; margin-bottom: .6rem; overflow: hidden;
}
details.file > summary {
  cursor: pointer; padding: .6rem .9rem; display: flex; flex-wrap: wrap;
  gap: .5rem; align-items: center; list-style: none;
}
details.file > summary::-webkit-details-marker { display: none; }
details.file > summary::before {
  content: "\\25B8"; color: var(--muted); transition: transform .12s ease;
  display: inline-block; width: .8rem;
}
details.file[open] > summary::before { transform: rotate(90deg); }
summary .path { font-family: ui-monospace, monospace; font-size: .88rem; }
summary .counts { margin-left: auto; display: flex; gap: .35rem; }

.finding { padding: .7rem .9rem .8rem 2rem; border-top: 1px solid var(--border); }
.finding .head { display: flex; flex-wrap: wrap; gap: .45rem; align-items: baseline; }
.finding .title { font-weight: 600; }
.finding .where { color: var(--muted); font-size: .82rem; }
.finding .ctx { color: var(--ink-2); font-size: .84rem; margin-top: .2rem; }
.finding pre {
  margin: .45rem 0 0; padding: .55rem .7rem; background: var(--plane);
  border: 1px solid var(--border); border-radius: 7px; font-size: .84rem;
  white-space: pre-wrap; overflow-x: auto; color: var(--ink-2);
}
.hidden { display: none !important; }
.empty { color: var(--muted); padding: 2rem 0; text-align: center; }
footer { color: var(--muted); font-size: .8rem; margin-top: 2rem;
         border-top: 1px solid var(--border); padding-top: .8rem; }
"""

_JS = """
(function () {
  var findings = Array.prototype.slice.call(document.querySelectorAll('.finding'));
  var groups = Array.prototype.slice.call(document.querySelectorAll('details.file'));
  var search = document.getElementById('q');
  var shown = document.getElementById('shown');
  var empty = document.getElementById('empty');
  var timer = null;

  function checked(name) {
    var out = {};
    document.querySelectorAll('input[data-facet="' + name + '"]').forEach(function (el) {
      if (el.checked) { out[el.value] = true; }
    });
    return out;
  }

  function apply() {
    var sev = checked('sev'), life = checked('life'), chk = checked('check');
    var q = search.value.trim().toLowerCase();
    var total = 0;

    findings.forEach(function (el) {
      var ok = sev[el.dataset.sev] && life[el.dataset.life] && chk[el.dataset.check]
               && (!q || el.dataset.search.indexOf(q) !== -1);
      el.classList.toggle('hidden', !ok);
      if (ok) { total++; }
    });

    groups.forEach(function (group) {
      var visible = group.querySelectorAll('.finding:not(.hidden)').length;
      group.classList.toggle('hidden', visible === 0);
      var badge = group.querySelector('.match-count');
      if (badge) { badge.textContent = visible; }
    });

    shown.textContent = total;
    empty.classList.toggle('hidden', total !== 0);
  }

  document.querySelectorAll('input[data-facet]').forEach(function (el) {
    el.addEventListener('change', apply);
  });
  search.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(apply, 120);
  });
  document.getElementById('expand').addEventListener('click', function () {
    var open = groups.some(function (g) { return !g.open; });
    groups.forEach(function (g) { g.open = open; });
    this.textContent = open ? 'Collapse all' : 'Expand all';
  });
  document.getElementById('reset').addEventListener('click', function () {
    document.querySelectorAll('input[data-facet]').forEach(function (el) {
      el.checked = true;
    });
    search.value = '';
    apply();
  });
  apply();
})();
"""


@dataclass
class ReportMeta:
    """Context describing what was compared."""

    title: str = "Zephyr API compatibility report"
    base: str | None = None
    head: str | None = None
    unversioned_is: str | None = None
    generated: str | None = None
    command: str | None = None


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _tile(count: int, label: str, tone: str = "") -> str:
    klass = f"tile is-{tone}" if tone else "tile"
    return (
        f'<div class="{klass}"><div class="n">{count}</div><div class="k">{_esc(label)}</div></div>'
    )


def _badge(text: str, klass: str = "") -> str:
    return (
        f'<span class="badge {klass}"><span class="dot"></span>{_esc(text)}</span>'
        if klass
        else f'<span class="badge">{_esc(text)}</span>'
    )


def _checkbox(facet: str, value: str, label: str, count: int) -> str:
    return (
        f'<label class="chk"><input type="checkbox" checked data-facet="{_esc(facet)}" '
        f'value="{_esc(value)}">{_esc(label)} <span class="badge">{count}</span></label>'
    )


def _is_silent(finding: Finding) -> bool:
    return finding.title.startswith(SILENT_MARKER)


def _render_finding(finding: Finding) -> str:
    tone, label = _SEVERITY_STYLE[finding.severity]
    silent = _is_silent(finding)
    title = finding.title[len(SILENT_MARKER) :].strip() if silent else finding.title

    badges = [_badge(label, f"sev-{tone}")]
    if silent:
        # The dangerous class: still compiles, behaves differently.
        badges.append(_badge("Silent behavior change", "silent"))

    context = []
    if finding.symbol:
        context.append(f"<code>{_esc(finding.symbol)}</code>")
    if finding.group:
        state = f" &middot; {_esc(finding.lifecycle)}" if finding.lifecycle else ""
        context.append(f"group <code>{_esc(finding.group)}</code>{state}")

    # Lowercased once at build time so filtering stays a substring test.
    #
    # The detail text is deliberately left out. It is by far the longest field
    # and is largely boilerplate repeated across findings, so including it can
    # roughly double the size of a large report while adding little that the
    # title does not already carry.
    haystack = " ".join(
        str(part).lower()
        for part in (
            finding.title,
            finding.symbol,
            finding.group,
            finding.file,
            finding.check,
        )
        if part
    )

    line = f'<span class="where">:{finding.line}</span>' if finding.line else ""
    detail = f"<pre>{_esc(finding.detail)}</pre>" if finding.detail else ""
    context_html = '<div class="ctx">' + " &middot; ".join(context) + "</div>" if context else ""
    lifecycle = _esc(finding.lifecycle or "unknown")

    return (
        f'<div class="finding" data-sev="{_esc(finding.severity.value)}" '
        f'data-life="{lifecycle}" '
        f'data-check="{_esc(finding.check)}" data-search="{_esc(haystack)}">'
        f'<div class="head">{"".join(badges)}'
        f'<span class="title">{_esc(title)}</span>{line}</div>'
        f"{context_html}{detail}</div>"
    )


def format_html(findings: list[Finding], meta: ReportMeta | None = None) -> str:
    """Render findings as a standalone HTML page."""
    meta = meta or ReportMeta()
    findings = sort_findings(findings)

    by_severity = collections.Counter(f.severity for f in findings)
    by_lifecycle = collections.Counter(f.lifecycle or "unknown" for f in findings)
    by_check = collections.Counter(f.check for f in findings)
    silent = sum(1 for f in findings if _is_silent(f))

    # Group by file, preserving the severity-first order within each group.
    grouped: dict[str, list[Finding]] = collections.defaultdict(list)
    for finding in findings:
        grouped[finding.file or "(no file)"].append(finding)

    tiles = [
        _tile(len(findings), "Findings"),
        _tile(by_severity[Severity.ERROR], "Errors", "critical"),
        _tile(by_severity[Severity.WARNING], "Warnings", "warning"),
        _tile(by_severity[Severity.NOTE], "Notes"),
        _tile(silent, "Silent changes", "serious"),
        _tile(len(grouped), "Files"),
    ]

    severity_filters = [
        _checkbox("sev", sev.value, _SEVERITY_STYLE[sev][1] + "s", by_severity[sev])
        for sev in Severity
        if by_severity[sev]
    ]
    # Build the lifecycle filters from what is actually present, not from the
    # known states alone. A finding that resolves to no group carries no
    # lifecycle, and a value with no checkbox is filtered out by the page for
    # good: it would never be visible again.
    known = [state.value for state in _LIFECYCLE_ORDER]
    present = [value for value in known if by_lifecycle[value]]
    present += sorted(value for value in by_lifecycle if value not in known)
    lifecycle_filters = [
        _checkbox("life", value, value.capitalize(), by_lifecycle[value]) for value in present
    ]
    check_filters = [
        _checkbox("check", name, name, count) for name, count in sorted(by_check.items())
    ]

    blocks = []
    for path, items in sorted(grouped.items()):
        counts = collections.Counter(f.severity for f in items)
        summary_badges = [
            _badge(
                f"{counts[sev]} {_SEVERITY_STYLE[sev][1].lower()}", f"sev-{_SEVERITY_STYLE[sev][0]}"
            )
            for sev in Severity
            if counts[sev]
        ]
        rendered = "".join(_render_finding(f) for f in items)
        blocks.append(
            f'<details class="file"><summary><span class="path">{_esc(path)}</span>'
            f'<span class="counts">{"".join(summary_badges)}'
            f'<span class="badge"><span class="match-count">{len(items)}</span>&nbsp;shown</span>'
            f"</span></summary>{rendered}</details>"
        )

    subtitle = ""
    if meta.base and meta.head:
        subtitle = (
            f'<p class="sub">Comparing <code>{_esc(meta.base)}</code> '
            f"&rarr; <code>{_esc(meta.head)}</code></p>"
        )

    meta_bits = []
    if meta.unversioned_is:
        meta_bits.append(f"Groups without @version treated as <b>{_esc(meta.unversioned_is)}</b>")
    if meta.generated:
        meta_bits.append(f"Generated {_esc(meta.generated)}")
    if meta.command:
        meta_bits.append(f"<code>{_esc(meta.command)}</code>")

    body = "".join(blocks) or ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(meta.title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
<h1>{_esc(meta.title)}</h1>
{subtitle}
<p class="meta">{" &middot; ".join(meta_bits)}</p>

<div class="tiles">{"".join(tiles)}</div>

<div class="panel">
  <div class="row">
    <span class="lbl">Search</span>
    <input type="search" id="q" placeholder="symbol, group, file or message&hellip;">
    <button id="expand" type="button">Expand all</button>
    <button id="reset" type="button">Reset filters</button>
  </div>
  <div class="row"><span class="lbl">Severity</span>{"".join(severity_filters)}</div>
  <div class="row"><span class="lbl">Lifecycle</span>{"".join(lifecycle_filters)}</div>
  <div class="row"><span class="lbl">Check</span>{"".join(check_filters)}</div>
  <div class="row"><span class="lbl">Showing</span>
    <span><b id="shown">{len(findings)}</b> of {len(findings)} findings</span>
  </div>
</div>

{body}
<div class="empty hidden" id="empty">No findings match the current filters.</div>

<footer>
Severity follows the lifecycle state of the enclosing Doxygen group: stable is an
error, unstable a warning, experimental a note. A <b>silent behavior change</b>
still compiles for downstream users and changes what the code does &mdash; those
deserve the closest reading. See doc/develop/api/api_lifecycle.rst.
</footer>
</div>
<script>{_JS}</script>
</body>
</html>
"""
