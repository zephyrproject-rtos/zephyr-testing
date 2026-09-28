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

A group with no `@version` of its own **inherits the nearest versioned
ancestor's**: the subgroups of a stable API are part of that API and carry the
same promise. `@ingroup` names the parent and wins over lexical nesting inside
another group's `@{ ... @}`; where neither leads to a versioned ancestor the
group is genuinely unversioned. This resolves 438 of the 967 untagged groups —
`thread_apis`, `timer_apis` and `clock_apis` all inherit `1.0.0` from
`kernel_apis`, and `gpio_interface_ext` inherits from `gpio_interface`.

See `doc/develop/api/api_lifecycle.rst` and `doc/develop/api/overview.rst`.

## Commands

```console
# What lifecycle state is every API group in?
$ ./scripts/ci/check_api_compat.py audit --summary
1236 API groups
  state           declared  effective
  experimental          85        136
  unstable             118        219
  stable                66        352
  unversioned          967        529

438 groups inherit their state from an enclosing group (35% of all groups).

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

## Proposing a lifecycle state

529 groups declare no `@version` and inherit none, which is the backlog
everything else in this package has to work around. `propose` narrows it by
gathering the evidence `api_lifecycle.rst` asks for and suggesting a state per
group.

```console
$ ./scripts/ci/check_api_compat.py propose include/zephyr/drivers/
$ ./scripts/ci/check_api_compat.py propose include/zephyr/net include/zephyr/fs
$ ./scripts/ci/check_api_compat.py propose -f html -o proposals.html
```

Each proposal shows its evidence and the tags to paste onto the `@defgroup`:

```
include/zephyr/drivers/w1.h:257: NOTE: proposes unstable for 'w1_data_link'
    Evidence (peripheral):
      - shipped in 11 releases since 3.2
      - 2 in-tree implementations
      - only 5 in-tree users, below the bar for stable
    Suggested tags on the @defgroup:
        @since 3.2
        @version 0.8.0
```

### The rules

An API is judged **peripheral** when its implementations can actually be
counted, and **hardware-agnostic** otherwise.

| Evidence | Proposal |
| --- | --- |
| not in any release yet | experimental |
| peripheral, <2 releases or <2 implementations | experimental |
| peripheral, ≥2 releases and ≥2 implementations | unstable |
| peripheral, ≥3 releases, ≥2 implementations, ≥10 users | **stable (candidate)** |
| agnostic, <2 releases | experimental |
| agnostic, ≥2 releases | unstable |
| agnostic, ≥3 releases and ≥10 users | **stable (candidate)** |

`--min-users` moves the last bar; `--include-versioned` also re-examines groups
that already declare a version — or inherit one — which surfaces disagreements.
Groups covered by an enclosing group are skipped by default and the number is
reported.

### What the evidence is, and where it lies

**Releases** come from git, following renames. Without `--follow` every header
looks 3.0-era, because that is when headers moved under `include/zephyr/`.
Release tags are the `vX.Y.0` tags whose timestamps increase in version order —
this clone carries a stray `v5.0.0` pointing at an unrelated commit, and real
LTS releases are tagged on branches that are not ancestors of main, so neither
plain sorting nor reachability alone is safe. Validated against the groups that
do declare `@since`: gpio resolves to 1.0, w1 to 3.2, nvmem to 4.3.

**Implementations** come from `DEVICE_API(<class>, …) = {`, where the class is
read from the header's `struct <class>_driver_api` rather than guessed from the
filename — `watchdog.h` declares class `wdt`, and `uart.h` keeps its vtable in a
nested `_internal.h`. Where a devicetree binding directory exists, the count is
capped by the number of distinct non-Zephyr vendor prefixes in its
`compatible:` values, which counts hardware rather than files. Test and
emulated drivers are excluded.

> About eighteen driver classes have **no vtable at all** — pinctrl, hwinfo,
> cache, timer, input — because they dispatch directly or through syscalls. For
> those the implementation count is *unknown*, not zero, and the API falls back
> to the hardware-agnostic rule. Treating it as zero would mark long-stable
> APIs experimental.

**Users** are files outside `include/` that include the header, excluding the
API's own implementation directories. That exclusion matters: `w1.h` has 18
includes, but 15 are its own drivers, so counting them made a niche API look
widely adopted enough to propose as stable.

### Limits

A **stable** proposal is a candidate, never a verdict. `api_lifecycle.rst` also
requires 100% test coverage, complete in-code documentation, and review at the
Architecture WG — none of which this measures.

Evidence is gathered per header, so several groups declared in one header share
it and receive the same proposal. Judge them individually.

The run costs two `git grep`s and a rename-following log per header, spread over
`-j` workers: roughly 90 seconds for `include/zephyr/drivers/`.

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

About 78% of groups declare no `@version`, but 45% of those inherit one from an
enclosing group, leaving 529 of 1236 genuinely unversioned. `audit` reports both
columns:

```console
$ ./scripts/ci/check_api_compat.py audit --summary
1236 API groups
  state           declared  effective
  experimental          85        136
  unstable             118        219
  stable                66        352
  unversioned          967        529

438 groups inherit their state from an enclosing group (35% of all groups).
```

What remains genuinely unversioned is treated as **stable** by
default, which fails closed: most untagged headers are long-lived core APIs
with the strongest de facto guarantees, so assuming "experimental" would exempt
exactly the wrong APIs. Override with `--unversioned-is` when triaging.

This does mean the signature check is noisy against today's tree. That is why
`group-metadata` only looks at newly added groups, and only those that do not
inherit a version: it stops the gap from growing while the backlog is worked
down. `audit --untagged-only` lists what is left.

## Running it in CI

`.github/workflows/api-compat.yml` runs both halves on a pull request and
reports findings as annotations. It builds one Doxygen tree per revision and
reuses that pair for the annotations, the job summary and an HTML artifact,
rather than paying for the builds three times.

Two notes on how it is configured.

It passes `--unversioned-is unstable`, which is deliberately weaker than this
package's fail-closed default. A large part of the tree is still untagged, and
grading all of it as stable would fail pull requests over APIs that never
promised anything; with this flag only an API that explicitly declares itself
stable can fail the job. Drop the flag once the untagged backlog is small
enough to gate on.

It needs no west workspace: every `INPUT` in `doc/zephyr.doxyfile.in` is
relative to the Zephyr repository, so a bare checkout is enough to build the
snapshots. That is why it is a good deal cheaper to set up than the
documentation workflows.

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
