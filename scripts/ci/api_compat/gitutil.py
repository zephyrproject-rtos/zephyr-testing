# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Small git helpers.

Deliberately standalone rather than reusing the helpers in check_compliance.py,
so that this package can be run on its own. A ComplianceTest wrapper can pass
its own COMMIT_RANGE and GIT_TOP straight through.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(*args: str, cwd: str | Path | None = None) -> str:
    """Run a git command and return its stdout."""
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as err:
        raise GitError("git executable not found") from err
    except subprocess.CalledProcessError as err:
        raise GitError(f"git {' '.join(args)} failed: {err.stderr.strip()}") from err
    return result.stdout


def git_top(cwd: str | Path | None = None) -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=cwd).strip())


def split_range(commit_range: str) -> tuple[str, str]:
    """Split a commit range into (base, head).

    Accepts the forms git itself accepts, including the open-ended
    "origin/main.." that Zephyr's compliance workflow uses.
    """
    for separator in ("...", ".."):
        if separator in commit_range:
            base, _, head = commit_range.partition(separator)
            return (base or "HEAD", head or "HEAD")
    # A bare revision means "changes introduced by this commit".
    return (f"{commit_range}^", commit_range)


def changed_files(
    commit_range: str,
    paths: list[str] | None = None,
    cwd: str | Path | None = None,
) -> list[str]:
    """Return repo-relative paths added or modified in the range.

    Deletions are excluded: a deleted header is handled by the signature
    comparison, which sees every symbol in it disappear.
    """
    args = ["diff", "--name-only", "--diff-filter=d", commit_range]
    if paths:
        args += ["--", *paths]
    return [line for line in git(*args, cwd=cwd).splitlines() if line]


def touched_files(
    base: str,
    head: str,
    cwd: str | Path | None = None,
) -> set[str]:
    """Every path the change touches, deletions included.

    Used to keep the signature comparison to files the change actually
    modified. Deletions matter here, unlike in changed_files(): a removed
    header is exactly where removed symbols are reported.
    """
    out = git("diff", "--name-only", f"{base}...{head}", cwd=cwd)
    return {line for line in out.splitlines() if line}


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(commit_range: str, path: str, cwd: str | Path | None = None) -> set[int]:
    """Return the set of line numbers added to a file, in post-image terms."""
    diff = git("diff", "-U0", commit_range, "--", path, cwd=cwd)
    lines: set[int] = set()
    for line in diff.splitlines():
        match = _HUNK_RE.match(line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            lines.update(range(start, start + count))
    return lines


def file_at_rev(rev: str, path: str, cwd: str | Path | None = None) -> str | None:
    """Return a file's contents at a revision, or None if absent there."""
    try:
        return git("show", f"{rev}:{path}", cwd=cwd)
    except GitError:
        return None
