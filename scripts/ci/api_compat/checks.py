# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Checks that need only the headers and the diff.

These two checks require no compiler and no Doxygen run, so they are cheap
enough to run on every pull request. The signature comparison, which does need
Doxygen output, lives in signature.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import apidoc, gitutil
from .apidoc import Lifecycle
from .findings import Finding, Severity

#: Headers whose groups define the public API surface.
DEFAULT_PATHS = ["include/zephyr/**/*.h", "include/zephyr/*.h"]

#: Defines the deprecation markers themselves; not an API user.
_EXCLUDED = ("include/zephyr/toolchain/",)

_DEPRECATION_RE = re.compile(r"__deprecated(?:_version)?\b|__DEPRECATED_MACRO\b|[@\\]deprecated\b")


def _is_relevant(path: str) -> bool:
    return (
        path.startswith("include/zephyr/")
        and path.endswith(".h")
        and not path.startswith(_EXCLUDED)
    )


def select_headers(commit_range: str | None, repo: Path, all_files: bool = False) -> list[str]:
    """Return the headers a check should look at."""
    if all_files:
        return sorted(
            str(p.relative_to(repo))
            for p in (repo / "include" / "zephyr").rglob("*.h")
            if _is_relevant(str(p.relative_to(repo)))
        )
    if commit_range is None:
        return []
    return [f for f in gitutil.changed_files(commit_range, cwd=repo) if _is_relevant(f)]


def check_group_metadata(
    commit_range: str | None,
    repo: Path,
    all_files: bool = False,
) -> list[Finding]:
    """Require newly declared Doxygen groups to carry valid @since/@version.

    Only groups actually introduced by the change are checked. Roughly 80% of
    the groups already in the tree carry no version at all, so checking every
    group would bury the signal; use all_files=True to produce that backlog
    report deliberately.

    A group nested inside a versioned group is not required to declare its own
    version: it is part of that API and inherits its state. Resolving that
    needs the whole tree, since @ingroup routinely names a parent declared in
    another header.
    """
    findings: list[Finding] = []
    check = "group-metadata"

    index = apidoc.index_headers(repo, select_headers(None, repo, all_files=True))

    for path in select_headers(commit_range, repo, all_files):
        full = repo / path
        if not full.exists():
            continue
        info = apidoc.scan_file(full, path)

        if all_files:
            new_lines = None
        else:
            new_lines = gitutil.added_lines(commit_range, path, cwd=repo)

        for group in info.groups.values():
            # Only complain about groups this change actually introduced.
            if new_lines is not None and group.line not in new_lines:
                continue

            resolution = index.resolve(group.name)

            common = {
                "check": check,
                "file": path,
                "line": group.line,
                "group": group.name,
                "lifecycle": resolution.lifecycle.value,
            }

            if group.version_raw is None:
                # Inheriting a version from an enclosing group is enough: the
                # subgroup is part of that API and shares its promise.
                if not resolution.inherited:
                    findings.append(
                        Finding(
                            severity=Severity.ERROR,
                            title=f"new API group '{group.name}' has no @version",
                            detail=(
                                "Every API group must declare its maturity with @version,\n"
                                "or sit inside a group that does.\n"
                                "Use 0.1.0 for a new experimental API. See "
                                "doc/develop/api/api_lifecycle.rst."
                            ),
                            **common,
                        )
                    )
            elif group.version is None:
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        title=f"'{group.name}' has a malformed @version",
                        detail=(
                            f"@version {group.version_raw} is not a semantic version.\n"
                            "Expected major.minor.patch, for example 0.1.0."
                        ),
                        **common,
                    )
                )
            elif not group.version.is_wellformed:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        title=f"'{group.name}' uses an undefined @version",
                        detail=(
                            f"@version {group.version_raw} maps to no lifecycle state in "
                            "doc/develop/api/overview.rst.\nA new experimental API should "
                            "use 0.1.0."
                        ),
                        **common,
                    )
                )

            if group.since is None:
                if not resolution.inherited:
                    findings.append(
                        Finding(
                            severity=Severity.ERROR,
                            title=f"new API group '{group.name}' has no @since",
                            detail=(
                                "Declare the Zephyr release that introduces the API, "
                                "for example '@since 4.3',\nor nest the group inside "
                                "one that already declares its lifecycle."
                            ),
                            **common,
                        )
                    )
            elif not apidoc.is_valid_since(group.since):
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        title=f"'{group.name}' has a malformed @since",
                        detail=(f"@since {group.since} should be a release number such as '4.3'."),
                        **common,
                    )
                )

    return findings


def check_deprecation(commit_range: str | None, repo: Path) -> list[Finding]:
    """Require a minor version bump when an API gains a deprecation.

    doc/develop/api/overview.rst states that deprecating anything within an API
    requires that API's minor version to be bumped. That is mechanically
    checkable from the diff alone, with no parsing of C.
    """
    findings: list[Finding] = []
    check = "deprecation-version"

    if commit_range is None:
        return findings

    base_rev, _ = gitutil.split_range(commit_range)

    for path in select_headers(commit_range, repo):
        full = repo / path
        if not full.exists():
            continue

        new_lines = gitutil.added_lines(commit_range, path, cwd=repo)
        if not new_lines:
            continue

        text = full.read_text(encoding="utf-8", errors="replace")
        head = apidoc.scan_header(text, path)
        lines = text.splitlines()

        # Collect the groups that gained a deprecation marker, keeping the
        # first line of each so the finding can point somewhere useful.
        touched: dict[str | None, int] = {}
        for lineno in sorted(new_lines):
            if lineno > len(lines):
                continue
            if not _DEPRECATION_RE.search(lines[lineno - 1]):
                continue
            group = head.group_at(lineno)
            touched.setdefault(group, lineno)

        if not touched:
            continue

        base_text = gitutil.file_at_rev(base_rev, path, cwd=repo)
        base = apidoc.scan_header(base_text, path) if base_text is not None else None

        for group_name, lineno in touched.items():
            if group_name is None:
                findings.append(
                    Finding(
                        check=check,
                        severity=Severity.WARNING,
                        title="deprecation outside any API group",
                        detail=(
                            "A deprecation marker was added here, but the symbol "
                            "belongs to no @defgroup,\nso its lifecycle state and "
                            "required version bump cannot be determined."
                        ),
                        file=path,
                        line=lineno,
                    )
                )
                continue

            group = head.groups.get(group_name)
            base_group = base.groups.get(group_name) if base else None
            lifecycle = group.lifecycle if group else Lifecycle.UNVERSIONED

            common = {
                "check": check,
                "file": path,
                "line": lineno,
                "group": group_name,
                "lifecycle": lifecycle.value,
            }

            # A group introduced by this very change cannot have bumped.
            if base_group is None:
                continue

            head_version = group.version if group else None
            base_version = base_group.version

            if head_version is None or base_version is None:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        title=f"cannot verify version bump for '{group_name}'",
                        detail=(
                            "This API gained a deprecation, but the group carries no "
                            "usable @version,\nso the required minor version bump "
                            "cannot be confirmed."
                        ),
                        **common,
                    )
                )
                continue

            bumped = (head_version.major, head_version.minor) > (
                base_version.major,
                base_version.minor,
            )
            if bumped:
                continue

            # Unstable and experimental APIs may be removed without any
            # deprecation period at all, so hold them to a softer standard.
            severity = Severity.ERROR if lifecycle.is_protected else Severity.WARNING
            findings.append(
                Finding(
                    severity=severity,
                    title=f"deprecation in '{group_name}' without a minor version bump",
                    detail=(
                        f"@version is still {base_version.raw}.\n"
                        "Deprecating an API requires bumping its minor version "
                        f"(to {base_version.major}.{base_version.minor + 1}.0).\n"
                        "See doc/develop/api/overview.rst."
                    ),
                    **common,
                )
            )

    return findings
