# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Command line interface for the API compatibility tooling."""

from __future__ import annotations

import argparse
import collections
import sys
from datetime import datetime
from pathlib import Path

from . import checks, gitutil
from .apidoc import Lifecycle
from .findings import FORMAT_CHOICES, FORMATTERS, Finding, Severity
from .propose import SUBSTANTIAL_USERS

_THRESHOLDS = {
    "error": (Severity.ERROR,),
    "warning": (Severity.ERROR, Severity.WARNING),
    "never": (),
}

#: Checks runnable without a Doxygen build.
CHEAP_CHECKS = ("group-metadata", "deprecation-version")
ALL_CHECKS = (*CHEAP_CHECKS, "signature")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-C",
        "--repo",
        type=Path,
        default=None,
        help="repository to inspect (default: the enclosing git tree)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=FORMAT_CHOICES,
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "--fail-on",
        choices=sorted(_THRESHOLDS),
        default="error",
        help="lowest severity that makes the run fail (default: error)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="write the report to a file instead of stdout",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_api_compat.py",
        description=(
            "Report changes to public API headers that break the guarantees "
            "implied by the API's documented lifecycle state."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check a change for API contract violations")
    _add_common(check)
    check.add_argument(
        "-c",
        "--commits",
        default="HEAD~1..HEAD",
        help="commit range to inspect (default: HEAD~1..HEAD)",
    )
    check.add_argument(
        "-m",
        "--run",
        action="append",
        choices=ALL_CHECKS,
        default=None,
        help="run only the named check (repeatable; default: all cheap checks)",
    )
    check.add_argument(
        "--base-xml",
        type=Path,
        default=None,
        help="Doxygen XML directory for the base revision (enables the signature check)",
    )
    check.add_argument(
        "--head-xml",
        type=Path,
        default=None,
        help="Doxygen XML directory for the changed revision",
    )

    audit = sub.add_parser("audit", help="report the lifecycle state of every API group")
    _add_common(audit)
    audit.add_argument(
        "--untagged-only",
        action="store_true",
        help="list only groups that carry no @version",
    )
    audit.add_argument(
        "--summary",
        action="store_true",
        help="print only per-state counts",
    )

    signature = sub.add_parser(
        "signature",
        help="compare two Doxygen XML trees directly",
    )
    _add_common(signature)
    signature.add_argument("base_xml", type=Path, help="Doxygen XML directory, base revision")
    signature.add_argument("head_xml", type=Path, help="Doxygen XML directory, changed revision")
    signature.add_argument(
        "--base-root",
        type=Path,
        default=None,
        help="source root the base XML was generated from, to relativize paths",
    )
    signature.add_argument(
        "--head-root",
        type=Path,
        default=None,
        help="source root the head XML was generated from",
    )
    signature.add_argument(
        "--changed-in",
        default=None,
        metavar="BASE..HEAD",
        help="limit findings to the files this commit range touches",
    )
    _add_scope(signature)
    _add_unversioned(signature)

    propose = sub.add_parser(
        "propose",
        help="propose a lifecycle state for API groups from tree evidence",
    )
    _add_common(propose)
    propose.add_argument(
        "paths",
        nargs="*",
        default=None,
        help="paths to inspect, e.g. include/zephyr/drivers/ (default: all of include/zephyr)",
    )
    propose.add_argument(
        "--include-extensions",
        action="store_true",
        help=(
            "also examine per-vendor and emulator headers nested under a driver "
            "class, which extend an API rather than declaring one"
        ),
    )
    propose.add_argument(
        "--include-versioned",
        action="store_true",
        help="also examine groups that already declare a @version",
    )
    propose.add_argument(
        "--min-users",
        type=int,
        default=None,
        help=f"in-tree users required for a stable proposal (default: {SUBSTANTIAL_USERS})",
    )
    propose.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=8,
        help="parallel git workers (default: 8)",
    )

    revs = sub.add_parser(
        "compare-revs",
        help="build Doxygen snapshots of two revisions and compare them",
    )
    _add_common(revs)
    revs.add_argument("base", help="base revision, for example origin/main")
    revs.add_argument("head", nargs="?", default="HEAD", help="revision to check (default: HEAD)")
    revs.add_argument(
        "--xml-dir",
        type=Path,
        default=None,
        help="keep the generated Doxygen output under this directory",
    )
    _add_scope(revs)
    _add_unversioned(revs)

    return parser.parse_args(argv)


def _add_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--all-files",
        action="store_true",
        help=(
            "report findings in every header, not only the ones the change "
            "touched. Doxygen expands macro-generated declarations "
            "inconsistently between runs, so this is noisy"
        ),
    )


def _add_unversioned(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--unversioned-is",
        choices=[state.value for state in Lifecycle],
        default=Lifecycle.STABLE.value,
        help=(
            "lifecycle to assume for groups with no @version (default: stable, which fails closed)"
        ),
    )


def _resolve_repo(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    return gitutil.git_top()


def _run_signature(
    base_xml: Path,
    head_xml: Path,
    unversioned: str,
    base_root: Path | None = None,
    head_root: Path | None = None,
    touched: set[str] | None = None,
) -> list[Finding]:
    # Imported lazily: it needs doxmlparser, which the cheap checks do not.
    from .signature import compare_xml_dirs, limit_to_files

    findings = compare_xml_dirs(
        base_xml,
        head_xml,
        unversioned_is=Lifecycle(unversioned),
        base_root=base_root,
        head_root=head_root,
    )
    if touched is not None:
        findings = limit_to_files(findings, touched)
    return findings


def _cmd_compare_revs(args: argparse.Namespace) -> list[Finding]:
    """Snapshot two revisions with Doxygen, then compare them."""
    import tempfile

    from .doxygen import snapshot_rev

    repo = _resolve_repo(args.repo)

    def run(destination: Path) -> list[Finding]:
        print(f"building Doxygen snapshot of {args.base} ...", file=sys.stderr)
        base_xml, _ = snapshot_rev(repo, args.base, destination / "base")
        print(f"building Doxygen snapshot of {args.head} ...", file=sys.stderr)
        head_xml, _ = snapshot_rev(repo, args.head, destination / "head")
        # Resolve declaration paths against the repository rather than the
        # snapshot worktrees, which snapshot_rev has already removed.
        touched = None
        if not args.all_files:
            touched = gitutil.touched_files(args.base, args.head, cwd=repo)
            print(f"limiting to the {len(touched)} files this change touches", file=sys.stderr)
        return _run_signature(base_xml, head_xml, args.unversioned_is, repo, repo, touched)

    if args.xml_dir:
        args.xml_dir.mkdir(parents=True, exist_ok=True)
        return run(args.xml_dir)
    with tempfile.TemporaryDirectory(prefix="api-compat-xml-") as tmp:
        return run(Path(tmp))


def _cmd_propose(args: argparse.Namespace) -> list[Finding]:
    """Propose lifecycle states for the groups under the given paths."""
    from . import propose as proposals
    from .evidence import gather_all
    from .history import release_tags

    repo = _resolve_repo(args.repo)

    if args.min_users is not None:
        proposals.SUBSTANTIAL_USERS = args.min_users

    roots = args.paths or ["include/zephyr"]
    headers = sorted(
        {
            str(path.relative_to(repo))
            for root in roots
            for path in sorted(
                (repo / root).rglob("*.h") if (repo / root).is_dir() else [repo / root]
            )
            if path.is_file()
        }
    )
    if not headers:
        raise SystemExit(f"no headers found under: {', '.join(roots)}")

    from .evidence import is_extension_header

    skipped = []
    if not args.include_extensions:
        skipped = [h for h in headers if is_extension_header(h)]
        headers = [h for h in headers if not is_extension_header(h)]

    # Inheritance is resolved over the whole tree, not just the selected
    # paths: @ingroup routinely names a parent declared in another header.
    from . import apidoc
    from .checks import select_headers

    index = apidoc.index_headers(repo, select_headers(None, repo, all_files=True))

    releases = release_tags(repo)
    print(
        f"examining {len(headers)} headers against {len(releases)} releases ...",
        file=sys.stderr,
    )
    if skipped:
        # Never drop input silently: say what was left out and how to get it.
        print(
            f"skipping {len(skipped)} per-vendor or emulator headers that extend "
            "an API rather than declaring one; pass --include-extensions to "
            "examine them too",
            file=sys.stderr,
        )

    findings = []
    inherited = 0
    for evidence in gather_all(repo, headers, releases, workers=args.jobs):
        resolution = index.resolve(evidence.group.name)
        if resolution.inherited and not args.include_versioned:
            # Already covered by an enclosing group; nothing to propose.
            inherited += 1
            continue
        if evidence.declared is not Lifecycle.UNVERSIONED and not args.include_versioned:
            continue
        findings.append(proposals.to_finding(evidence, proposals.classify(evidence)))

    if inherited:
        print(
            f"skipping {inherited} groups that inherit a version from an enclosing group",
            file=sys.stderr,
        )
    return findings


def _cmd_check(args: argparse.Namespace) -> list[Finding]:
    repo = _resolve_repo(args.repo)
    selected = set(args.run or CHEAP_CHECKS)
    findings: list[Finding] = []

    if "group-metadata" in selected:
        findings += checks.check_group_metadata(args.commits, repo)
    if "deprecation-version" in selected:
        findings += checks.check_deprecation(args.commits, repo)

    if "signature" in selected or (args.base_xml and args.head_xml):
        if not (args.base_xml and args.head_xml):
            raise SystemExit(
                "the signature check needs both --base-xml and --head-xml; "
                "see the module docstring for how to produce them"
            )
        findings += _run_signature(args.base_xml, args.head_xml, Lifecycle.STABLE.value)

    return findings


def _cmd_audit(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    from . import apidoc

    headers = checks.select_headers(None, repo, all_files=True)
    index = apidoc.index_headers(repo, headers)

    rows = []
    declared: collections.Counter = collections.Counter()
    effective: collections.Counter = collections.Counter()
    inherited = 0

    for name, group in index.groups.items():
        resolution = index.resolve(name)
        declared[group.lifecycle] += 1
        effective[resolution.lifecycle] += 1
        if resolution.inherited:
            inherited += 1

        if args.untagged_only and (group.version_raw or resolution.inherited):
            continue

        source = ""
        if resolution.inherited:
            source = f"<- {resolution.source}"
        rows.append(
            (
                name,
                resolution.lifecycle.value,
                group.version_raw or "-",
                group.since or "-",
                source,
                f"{group.file}:{group.line}",
            )
        )

    total = sum(declared.values())
    if not args.summary:
        width = max((len(r[0]) for r in rows), default=4)
        for name, state, version, since, source, where in sorted(rows):
            print(f"{name:<{width}}  {state:<12}  {version:<8}  {since:<6}  {source:<28}  {where}")
        print()

    print(f"{total} API groups")
    print(f"{'':2}{'state':<14} {'declared':>9} {'effective':>10}")
    for state in apidoc.Lifecycle:
        if declared[state] or effective[state]:
            print(f"{'':2}{state.value:<14} {declared[state]:>9} {effective[state]:>10}")
    print(
        f"\n{inherited} groups inherit their state from an enclosing group "
        f"({100 * inherited / total:.0f}% of all groups)."
    )
    return 0


def _emit(findings: list[Finding], args: argparse.Namespace) -> None:
    """Render the findings and send them to a file or to stdout."""
    if args.format == "html":
        from .report import ReportMeta, format_html

        meta = ReportMeta(
            base=getattr(args, "base", None) or getattr(args, "commits", None),
            head=getattr(args, "head", None),
            unversioned_is=getattr(args, "unversioned_is", None),
            generated=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            command=" ".join(["check_api_compat.py", *sys.argv[1:]]),
        )
        text = format_html(findings, meta)
    else:
        text = FORMATTERS[args.format](findings)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {len(findings)} findings to {args.output}", file=sys.stderr)
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.command == "audit":
        return _cmd_audit(args)

    if args.command == "signature":
        touched = None
        if args.changed_in and not args.all_files:
            base, head = gitutil.split_range(args.changed_in)
            touched = gitutil.touched_files(base, head, cwd=_resolve_repo(args.repo))
        findings = _run_signature(
            args.base_xml,
            args.head_xml,
            args.unversioned_is,
            args.base_root,
            args.head_root,
            touched,
        )
    elif args.command == "propose":
        findings = _cmd_propose(args)
    elif args.command == "compare-revs":
        findings = _cmd_compare_revs(args)
    else:
        findings = _cmd_check(args)

    _emit(findings, args)

    failing = _THRESHOLDS[args.fail_on]
    return 1 if any(f.severity in failing for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
