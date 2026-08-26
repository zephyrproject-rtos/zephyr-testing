#!/usr/bin/env python3
#
# Copyright (c) 2026 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0

"""Build an integration branch from the merge list's ready pull requests.

Reads the merge list JSON feed, selects the PRs that are ready for
the requested target branch, fetches each PR head and merges them one by one
with --no-ff onto a new branch based on the target. Conflicting PRs are
skipped. Writes a JSON report (including each PR's head SHA as tested) and a
Markdown PR body with a table of what was merged and what was not.

Intended to run inside a checkout of the target repository, in CI or locally.
"""

import argparse
import json
import subprocess
import sys
import urllib.request

MERGE_LIST_URL = "https://merge-list.zephyrproject.io/merge_list.json"


def load_merge_list(url):
    """Return the merge list document (dict with 'pull_requests', 'freeze_mode', ...)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "zephyr-integration-train"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        doc = json.load(resp)
    if not isinstance(doc, dict) or "pull_requests" not in doc:
        sys.exit("error: unexpected merge list format")
    return doc


def git(*args, check=True, capture=True):
    r = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if check and r.returncode:
        sys.exit(f"error: git {' '.join(args)} failed:\n{r.stdout}")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", default="main", help="target branch (default: main)")
    ap.add_argument(
        "--remote",
        default="origin",
        help="remote name or URL of the repository the PRs and the target "
        "branch are fetched from (default: origin)",
    )
    ap.add_argument(
        "--branch", required=True, help="name of the integration branch to create"
    )
    ap.add_argument(
        "--max-prs", type=int, default=0, help="cap the train size (0 = no cap)"
    )
    ap.add_argument(
        "--prs",
        nargs="*",
        type=int,
        help="explicit PR numbers instead of the merge list",
    )
    ap.add_argument("--url", default=MERGE_LIST_URL)
    ap.add_argument("--report", default="integration-report.json")
    ap.add_argument(
        "--body", default="integration-body.md", help="Markdown PR body output"
    )
    ap.add_argument(
        "--repo",
        default="zephyrproject-rtos/zephyr",
        help="owner/name of the repository the PRs belong to, for links",
    )
    args = ap.parse_args()

    doc = {"freeze_mode": False, "updated": None}
    if args.prs:
        prs = [
            {"number": n, "title": "", "author": "", "status": "explicit"}
            for n in args.prs
        ]
        skipped = []
    else:
        doc = load_merge_list(args.url)
        rows = [r for r in doc["pull_requests"] if r.get("base") == args.target]
        prs = [r for r in rows if r.get("status") == "ready"]
        skipped = [r for r in rows if r.get("status") != "ready"]
        if doc.get("freeze_mode"):
            print("merge list is in freeze mode; only hotfix PRs are ready")
    prs.sort(key=lambda p: p["number"])
    dropped = []
    if args.max_prs and len(prs) > args.max_prs:
        dropped, prs = prs[args.max_prs :], prs[: args.max_prs]
        print(
            f"train capped at {args.max_prs}; {len(dropped)} ready PRs left for the next train"
        )
    if not prs:
        print(f"no ready PRs for '{args.target}'")
        with open(args.report, "w") as f:
            json.dump(
                {"target": args.target, "merged": [], "failed": [], "empty": True}, f
            )
        return 0

    print(f"{len(prs)} ready PR(s) for '{args.target}':")
    for p in prs:
        print(f"  #{p['number']:<7} {p['title']}")

    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        sys.exit(
            "error: working tree has uncommitted changes; refusing to build the train"
        )

    # --remote may be a URL (e.g. when running from a different repository
    # than the one the PRs live in); keep the fetched refs under a fixed name.
    refspecs = [f"+refs/heads/{args.target}:refs/integration/base/{args.target}"]
    refspecs += [
        f"+refs/pull/{p['number']}/head:refs/integration/pr/{p['number']}" for p in prs
    ]
    git("fetch", "--no-tags", args.remote, *refspecs, capture=False)
    base = git("rev-parse", f"refs/integration/base/{args.target}").stdout.strip()
    git("checkout", "-q", "-B", args.branch, base)

    merged, failed = [], []
    for p in prs:
        n = p["number"]
        ref = f"refs/integration/pr/{n}"
        head = git("rev-parse", ref).stdout.strip()
        listed = (p.get("head") or {}).get("sha")
        p["head_sha"] = head
        p["moved_since_listed"] = bool(listed and listed != head)
        if p["moved_since_listed"]:
            print(f"  note    #{n} head moved since the merge list was generated")
        r = git(
            "merge",
            "--no-ff",
            "--no-edit",
            "-m",
            f"Merge PR #{n}: {p['title']}".strip(),
            ref,
            check=False,
        )
        if r.returncode == 0:
            p["merge_commit"] = git("rev-parse", "HEAD").stdout.strip()
            merged.append(p)
            print(f"  merged  #{n} ({head[:10]})")
        else:
            files = git(
                "diff", "--name-only", "--diff-filter=U", check=False
            ).stdout.split()
            git("merge", "--abort", check=False)
            p["conflicts"] = files
            p["error"] = r.stdout.strip()[-400:]
            failed.append(p)
            if files:
                print(f"  FAILED  #{n}: conflicts in " + ", ".join(files))
            else:
                print(
                    f"  FAILED  #{n}: {p['error'].splitlines()[-1] if p['error'] else 'unknown'}"
                )

    head = git("rev-parse", "HEAD").stdout.strip()
    report = {
        "target": args.target,
        "base": base,
        "branch": args.branch,
        "head": head,
        "repo": args.repo,
        "merge_list_updated": doc.get("updated"),
        "freeze_mode": bool(doc.get("freeze_mode")),
        "merged": merged,
        "failed": failed,
        "deferred": dropped,
        "not_ready": [
            {"number": s["number"], "title": s["title"], "status": s["status"]}
            for s in skipped
        ],
    }
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2)
    with open(args.body, "w") as f:
        f.write(render_body(report))
    print(
        f"\n{len(merged)} merged, {len(failed)} failed; {args.branch} at {head[:12]} "
        f"(base {base[:12]})"
    )
    return 0


def render_body(rep):
    repo = rep["repo"]

    def link(n):
        return f"{n}"
        #return f"[#{n}](https://github.com/{repo}/pull/{n})"

    def cell(text):
        return text.replace("|", "\\|")

    out = [
        "<!-- integration-train -->",
        f"Integration train for `{rep['target']}`: every pull request the "
        f"[merge list](https://merge-list.zephyrproject.io) reported as ready"
        + (f" at {rep['merge_list_updated']}" if rep.get("merge_list_updated") else "")
        + f", merged in ascending PR order onto `{rep['target']}` at `{rep['base'][:12]}`."
        + (" The merge list was in **freeze mode**." if rep.get("freeze_mode") else ""),
        "",
        "**Do not merge this PR.** It exists only to run CI on the combined result. "
        "If it is green, the PRs below are merged individually; if it is red, the "
        "culprit is found by bisecting the `--no-ff` merge commits on this branch.",
        "",
        f"## Merged ({len(rep['merged'])})",
        "",
        "| PR | Title | Author | Head (as tested) |",
        "|---|---|---|---|",
    ]
    for p in rep["merged"]:
        #author = f"@{p['author']}" if p.get("author") else ""
        author = f"{p['author']}" if p.get("author") else ""

        out.append(
            f"| {link(p['number'])} | {cell(p['title'])} | {author} | "
            f"`{p['head_sha'][:10]}` |"
        )
    if rep["failed"]:
        out += [
            "",
            f"## Could not be merged ({len(rep['failed'])})",
            "",
            "These are ready on the merge list but conflict with an earlier PR in "
            "the train. They need a rebase and will ride the next train.",
            "",
            "| PR | Title | Conflicting files |",
            "|---|---|---|",
        ]
        for p in rep["failed"]:
            out.append(
                f"| {link(p['number'])} | {cell(p['title'])} | "
                f"{', '.join(f'`{c}`' for c in p['conflicts']) or '-'} |"
            )
    if rep.get("deferred"):
        out += [
            "",
            f"## Deferred to the next train ({len(rep['deferred'])})",
            "",
            ", ".join(link(p["number"]) for p in rep["deferred"]),
        ]
    out += [
        "",
        "<details><summary>Reproduce locally</summary>",
        "",
        "```",
        f"scripts/ci/integration_train.py --target {rep['target']} "
        f"--branch {rep['branch']} --prs "
        + " ".join(str(p["number"]) for p in rep["merged"]),
        "```",
        "</details>",
        "",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
