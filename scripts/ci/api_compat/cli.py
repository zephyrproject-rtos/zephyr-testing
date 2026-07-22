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
    _add_unversioned(signature)

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
    _add_unversioned(revs)

    return parser.parse_args(argv)


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
) -> list[Finding]:
    # Imported lazily: it needs doxmlparser, which the cheap checks do not.
    from .signature import compare_xml_dirs

    return compare_xml_dirs(
        base_xml,
        head_xml,
        unversioned_is=Lifecycle(unversioned),
        base_root=base_root,
        head_root=head_root,
    )


def _cmd_compare_revs(args: argparse.Namespace) -> list[Finding]:
    """Snapshot two revisions with Doxygen, then compare them."""
    import tempfile

    from .doxygen import snapshot_rev

    repo = _resolve_repo(args.repo)

    def run(destination: Path) -> list[Finding]:
        print(f"building Doxygen snapshot of {args.base} ...", file=sys.stderr)
        base_xml, base_root = snapshot_rev(repo, args.base, destination / "base")
        print(f"building Doxygen snapshot of {args.head} ...", file=sys.stderr)
        head_xml, head_root = snapshot_rev(repo, args.head, destination / "head")
        return _run_signature(base_xml, head_xml, args.unversioned_is, base_root, head_root)

    if args.xml_dir:
        args.xml_dir.mkdir(parents=True, exist_ok=True)
        return run(args.xml_dir)
    with tempfile.TemporaryDirectory(prefix="api-compat-xml-") as tmp:
        return run(Path(tmp))


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

    rows = []
    counts: collections.Counter = collections.Counter()
    for path in checks.select_headers(None, repo, all_files=True):
        info = apidoc.scan_file(repo / path, path)
        for group in info.groups.values():
            counts[group.lifecycle] += 1
            if args.untagged_only and group.version_raw is not None:
                continue
            rows.append(
                (
                    group.name,
                    group.lifecycle.value,
                    group.version_raw or "-",
                    group.since or "-",
                    f"{path}:{group.line}",
                )
            )

    total = sum(counts.values())
    if not args.summary:
        width = max((len(r[0]) for r in rows), default=4)
        for name, state, version, since, where in sorted(rows):
            print(f"{name:<{width}}  {state:<12}  {version:<8}  {since:<6}  {where}")
        print()

    print(f"{total} API groups")
    for state in Lifecycle:
        count = counts[state]
        if count:
            print(f"  {state.value:<14} {count:5d}  ({100 * count / total:.1f}%)")
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
        findings = _run_signature(
            args.base_xml,
            args.head_xml,
            args.unversioned_is,
            args.base_root,
            args.head_root,
        )
    elif args.command == "compare-revs":
        findings = _cmd_compare_revs(args)
    else:
        findings = _cmd_check(args)

    _emit(findings, args)

    failing = _THRESHOLDS[args.fail_on]
    return 1 if any(f.severity in failing for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
