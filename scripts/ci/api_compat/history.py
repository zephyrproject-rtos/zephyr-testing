# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""How long an API has existed, measured in Zephyr releases.

The lifecycle criteria in doc/develop/api/api_lifecycle.rst are partly about
time: promotion to stable wants an API "in use and available in at least two
development releases". For a group that carries no @since, that has to come
from git.

Two traps make the naive query wrong:

* Headers were moved under include/zephyr/ during the 3.0 cycle, so the commit
  that adds a path is usually the move, not the API. ``git log --follow`` is
  required; without it every header looks 3.0-era.
* Not every ``vX.Y.0`` tag is a release of this branch. A stray tag can sit far
  ahead in version order while pointing at an unrelated commit, and some real
  releases (LTS ones) are tagged on branches that are not ancestors of main, so
  filtering by reachability would discard them. Sorting by version and then
  keeping only the tags whose timestamps increase drops the former without
  losing the latter.

Validated against the groups that do declare @since: gpio.h resolves to v1.0.0
(@since 1.0), w1.h to v3.2.0 (@since 3.2) and nvmem.h to v4.3.0 (@since 4.3).
"""

from __future__ import annotations

import concurrent.futures
import re
from dataclasses import dataclass
from pathlib import Path

from .gitutil import GitError, git

_RELEASE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.0$")


@dataclass(frozen=True)
class Release:
    name: str
    major: int
    minor: int
    timestamp: int

    @property
    def short(self) -> str:
        """The form @since uses, for example "3.2"."""
        return f"{self.major}.{self.minor}"


def release_tags(repo: Path) -> list[Release]:
    """Return this branch's release tags, oldest first."""
    try:
        raw = git("tag", "--list", "v*", cwd=repo).split()
    except GitError:
        return []

    candidates = []
    for name in raw:
        match = _RELEASE_TAG_RE.match(name)
        if match:
            candidates.append((int(match.group(1)), int(match.group(2)), name))
    candidates.sort()

    releases: list[Release] = []
    for major, minor, name in candidates:
        try:
            stamp = int(git("log", "-1", "--format=%ct", name, cwd=repo).strip())
        except (GitError, ValueError):
            continue
        # Keep only tags that move forward in time. A tag whose version sorts
        # ahead of everything but whose commit predates the real releases is
        # not a release of this branch.
        if releases and stamp <= releases[-1].timestamp:
            continue
        releases.append(Release(name, major, minor, stamp))
    return releases


def added_timestamp(repo: Path, path: str) -> int | None:
    """When the file was first added, following renames."""
    try:
        out = git(
            "log", "--follow", "--diff-filter=A", "--format=%ct", "-1", "--", path, cwd=repo
        ).strip()
    except GitError:
        return None
    # A file added more than once (deleted and restored) yields several lines.
    first = out.splitlines()[-1] if out else ""
    return int(first) if first.isdigit() else None


def added_timestamps(repo: Path, paths: list[str], workers: int = 8) -> dict[str, int | None]:
    """Timestamps for many paths.

    ``--follow`` costs roughly a second per file on a tree this size, so the
    calls are spread over a small thread pool. They are independent git
    invocations, so there is nothing to serialize.
    """
    results: dict[str, int | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(added_timestamp, repo, path): path for path in paths}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()
    return results


@dataclass(frozen=True)
class Age:
    """Where a file sits in the release timeline."""

    #: First release that contained it, or None if it has never shipped.
    first: Release | None
    #: Number of releases it has appeared in, counting `first`.
    releases: int

    @property
    def shipped(self) -> bool:
        return self.first is not None


def age_of(releases: list[Release], added: int | None) -> Age:
    """Locate an add timestamp in the release timeline."""
    if added is None or not releases:
        return Age(None, 0)
    for index, release in enumerate(releases):
        if release.timestamp >= added:
            return Age(release, len(releases) - index)
    # Added after the most recent release: it has not shipped yet.
    return Age(None, 0)
