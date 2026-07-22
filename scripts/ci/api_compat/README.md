<!--
SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
SPDX-License-Identifier: Apache-2.0
-->

# API compatibility tooling

Reports changes to public API headers that break the guarantees implied by the
API's documented lifecycle state.

## Why the diff is not enough

Two separate questions have to be answered to judge an API change, and a
textual diff answers neither reliably.

**What actually changed.** A line-based diff misses changes that never touch a
prototype: a reordered struct field, an enumerator inserted mid-enum, a changed
`#define` value, a retyped callback. All of these keep compiling and change
behaviour at run time. It also reports pure reflow as a signature change. What
is needed is a comparison of *parsed declarations*.

**Whether the change was allowed.** Adding a parameter to a function is routine
churn in an experimental API and a process violation in a stable one. That
verdict is not in the diff at all: it comes from the lifecycle state, which
Zephyr encodes as a semantic version on the owning Doxygen group.

| `@version`    | State        | A breaking change is             |
| ------------- | ------------ | -------------------------------- |
| `0.1.z`       | experimental | expected, no announcement needed |
| `0.y.z, y>1`  | unstable     | allowed, explicitly unannounced  |
| `x.y.z, x>=1` | stable       | a contract violation             |

See `doc/develop/api/api_lifecycle.rst` and `doc/develop/api/overview.rst`.

## Commands

```console
# What lifecycle state is every API group in?
$ ./scripts/ci/check_api_compat.py audit --summary
1244 API groups
  experimental      85  (6.8%)
  unstable         117  (9.4%)
  stable            67  (5.4%)
  unversioned      975  (78.4%)

# Cheap checks on a branch: no Doxygen, no compiler.
$ ./scripts/ci/check_api_compat.py check -c origin/main..

# Full symbol comparison. Builds a Doxygen snapshot per revision, so it
# takes a few minutes.
$ ./scripts/ci/check_api_compat.py compare-revs origin/main HEAD

# Same, against XML trees produced elsewhere (a docs build, say).
$ ./scripts/ci/check_api_compat.py signature base/xml head/xml
```

`--format github` emits workflow annotations, `--format json` feeds other
tooling, and `--fail-on {error,warning,never}` sets the exit status.

## HTML report

`--format html` writes a standalone page, which is the practical way to read a
release-to-release comparison: those run to thousands of findings.

```console
$ ./scripts/ci/check_api_compat.py compare-revs v4.4.0 HEAD -f html -o api-report.html
```

Findings are grouped by file and collapsed, with client-side filters for
severity, lifecycle state and check, plus a search box over symbol, group, file
and message. Summary tiles count errors, warnings, notes and — most usefully —
**silent behaviour changes**, the ones that still compile.

The page has no external references at all: no CDN, no fonts, no remote scripts.
It opens straight from disk and can be attached to a CI job as an artifact. It
follows the viewer's light or dark theme, and every severity is labelled in text
as well as colour.

Size scales with the finding count: roughly 5 MB for 6000 findings. Large but
workable; `--fail-on never` keeps the exit status clean when generating a report
for review rather than gating on it.

## The checks

`group-metadata` and `deprecation-version` need only the headers and the diff.
`signature` needs a Doxygen XML tree per revision.

* **group-metadata** — a newly declared `@defgroup` must carry a well-formed
  `@since` and `@version`. Only groups the change actually introduces are
  checked; see the note on coverage below.
* **deprecation-version** — `overview.rst` requires an API's minor version to
  be bumped when anything in it is deprecated. Adding `__deprecated`,
  `__deprecated_version()`, `__DEPRECATED_MACRO` or `@deprecated` without that
  bump is reported.
* **signature** — compares every public symbol between two revisions, and
  splits findings by how loudly they fail:

  * *loud* — the build breaks: removals, parameter-count changes, macro arity
    changes. Bad, but self-announcing.
  * *silent* — the code still compiles and behaves differently: changed macro
    values, renumbered enumerators, reordered struct fields, retyped
    parameters. These are the dangerous ones, and the main reason a textual
    diff is not sufficient.

Severity follows the lifecycle: stable is an error, unstable a warning,
experimental a note.

## Why Doxygen XML rather than a C parser

The signature comparison consumes Doxygen's XML because it already supplies,
correlated with each other, the three things needed: normalized declarations
including struct field order, group membership resolved through
`@addtogroup`/`@{ @}` nesting, and the `@since`/`@version` tags. A C parser
would have to resolve include paths and Kconfig-dependent preprocessor state to
get the same result. `doc/zephyr.doxyfile.in` is reused directly, so a snapshot
sees the same inputs and `PREDEFINED` macros as the documentation build.

Header-level parsing (`apidoc.py`) is deliberately limited to Doxygen comment
blocks and never tries to understand C.

### Doxygen output is not stable across runs

Doxygen expands macros inconsistently between runs: the same unmodified
declaration can render as `DSP_FUNC_SCOPE void` in one snapshot and `void` in
the next, or as `_Bool` rather than `bool`. Comparing snapshots literally
therefore reports changes in files that were never touched — on one measured
`compare-revs` run, 11 of 13 findings were this artefact.

`_normalize_type()` in `signature.py` removes the decorations responsible
before comparing. If a spurious "return type changed" finding appears for an
unmodified file, the fix is normally to add the offending macro to
`_DECORATIONS`. Qualifiers a caller can observe — `const`, `volatile` — must
never be added there.

## Unversioned groups

About 78% of groups carry no `@version`. Those are treated as **stable** by
default, which fails closed: most untagged headers are long-lived core APIs
with the strongest de facto guarantees, so assuming "experimental" would exempt
exactly the wrong APIs. Override with `--unversioned-is` when triaging.

This does mean the signature check is noisy against today's tree. That is why
`group-metadata` only looks at newly added groups: it stops the gap from
growing while the backlog is worked down. `audit --untagged-only` lists it.

## Wiring into check_compliance.py

The package has no dependency on `check_compliance.py`, so a wrapper is small.
Note that new checks are blocking by default — only `ClangFormat` and
`LicenseAndCopyrightCheck` are listed as warnings in the `check-warns` step of
`.github/workflows/compliance.yml` — so a new check should start in that
`warns=(...)` array until its false-positive rate is known.

```python
class ApiCompatCheck(ComplianceTest):
    """Public API headers must honour their documented lifecycle state."""

    name = "ApiCompat"
    doc = zephyr_doc_detail_builder("/develop/api/api_lifecycle.html")
    path_hint = "<zephyr-base>"

    def run(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from api_compat import checks

        repo = Path(GIT_TOP)
        findings = checks.check_group_metadata(COMMIT_RANGE, repo)
        findings += checks.check_deprecation(COMMIT_RANGE, repo)

        for finding in findings:
            self.fmtd_failure(
                finding.severity.value,      # "error" or "warning"
                self.name,
                finding.file,
                line=finding.line,
                desc=f"{finding.title}\n{finding.detail}",
            )
```

`Severity.NOTE` has no `fmtd_failure` equivalent and should be dropped or
mapped to `"warning"`. The `signature` check needs two Doxygen builds and so
belongs in the documentation workflow rather than the compliance one.

## Tests

```console
$ PYTHONPATH=./scripts/tests pytest scripts/tests/ci/test_api_compat.py
```
