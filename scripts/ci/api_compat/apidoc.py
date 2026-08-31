# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Extraction of API lifecycle metadata from Zephyr headers.

Zephyr declares an API's maturity with @since and @version on the Doxygen
group that owns the API, never on individual symbols. To decide whether a
change to a given function is permitted, the function must first be resolved
to its enclosing group. Groups open with

    /**
     * @defgroup foo_interface Foo
     * @since 3.7
     * @version 0.1.0
     * @{
     */

and close with a matching @} block, so group membership is a lexical scope
that can be tracked with a stack while walking the header's comment blocks.

Parsing here is intentionally limited to Doxygen comment blocks: it never
tries to understand C. That keeps it independent of include paths and of any
Kconfig-driven preprocessor state, which a real C parser would need. Symbol
level signature comparison is handled separately in signature.py, from
Doxygen's own XML output.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field


class Lifecycle(enum.Enum):
    """API maturity, as derived from the group's @version."""

    EXPERIMENTAL = "experimental"
    UNSTABLE = "unstable"
    STABLE = "stable"
    #: Group carries no @version at all. Most of the tree is in this state.
    UNVERSIONED = "unversioned"

    @property
    def is_protected(self) -> bool:
        """Whether breaking changes to this API require the RFC process."""
        return self in (Lifecycle.STABLE, Lifecycle.UNVERSIONED)


@dataclass(frozen=True)
class Version:
    """A parsed @version value."""

    major: int
    minor: int
    patch: int
    raw: str

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def lifecycle(self) -> Lifecycle:
        # Per doc/develop/api/overview.rst: major >= 1 is stable, 0.1.z is
        # experimental, and anything else below 1.0.0 is unstable.
        if self.major >= 1:
            return Lifecycle.STABLE
        if self.minor <= 1:
            return Lifecycle.EXPERIMENTAL
        return Lifecycle.UNSTABLE

    @property
    def is_wellformed(self) -> bool:
        """Whether the value is one the lifecycle documentation describes.

        0.0.z is not a state overview.rst defines; it is treated as
        experimental but flagged, since it is most likely a typo for 0.1.z.
        """
        return not (self.major == 0 and self.minor == 0)


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
#: @since is documented as major.minor, but a few groups carry a patch too.
_SINCE_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def parse_version(raw: str | None) -> Version | None:
    """Parse a @version value, returning None if it is not valid semver."""
    if not raw:
        return None
    match = _VERSION_RE.match(raw.strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return Version(int(major), int(minor), int(patch or 0), raw.strip())


def is_valid_since(raw: str | None) -> bool:
    return bool(raw and _SINCE_RE.match(raw.strip()))


@dataclass
class Group:
    """A Doxygen group declared in a header."""

    name: str
    title: str
    file: str
    #: Line of the @defgroup / @addtogroup tag itself.
    line: int
    since: str | None = None
    version_raw: str | None = None
    #: True for @defgroup, False for @addtogroup (which only reopens a group).
    defines: bool = True
    #: Parent named by @ingroup, which may live in another header.
    parent: str | None = None
    #: Group whose @{ ... @} scope this declaration sits inside, if any.
    lexical_parent: str | None = None

    @property
    def version(self) -> Version | None:
        return parse_version(self.version_raw)

    @property
    def lifecycle(self) -> Lifecycle:
        """The state this group declares itself.

        Says nothing about a version inherited from a parent; use GroupIndex
        for that.
        """
        version = self.version
        return version.lifecycle if version else Lifecycle.UNVERSIONED

    @property
    def effective_parent(self) -> str | None:
        """The parent a version should be inherited from.

        @ingroup wins over lexical containment: it is the explicit statement of
        where the group belongs, and where the two disagree it names the more
        specific parent.
        """
        return self.parent or self.lexical_parent


@dataclass
class Scope:
    """A line range over which a group is the innermost open group."""

    group: str
    start: int
    end: int


@dataclass
class HeaderInfo:
    """Everything extracted from a single header."""

    path: str
    groups: dict[str, Group] = field(default_factory=dict)
    scopes: list[Scope] = field(default_factory=list)

    def group_at(self, line: int) -> str | None:
        """Return the innermost group enclosing the given line, if any."""
        best: Scope | None = None
        for scope in self.scopes:
            # Narrower span wins, so nested groups take precedence.
            if scope.start <= line <= scope.end and (
                best is None or (scope.end - scope.start) < (best.end - best.start)
            ):
                best = scope
        return best.group if best else None


# A Doxygen block comment. The negative lookahead rejects /**< member trailers,
# which never carry group tags.
_BLOCK_RE = re.compile(r"/\*\*(?!<)(.*?)\*/", re.DOTALL)

# Group tags and scope braces, matched together so that they can be replayed in
# source order. A single comment block may declare more than one group and open
# or close several scopes, so position matters: @since and @version bind to the
# @defgroup that precedes them.
_TOKEN_RE = re.compile(
    r"[@\\](?P<tag>defgroup|addtogroup|ingroup|since|version)[ \t]+(?P<arg>\S+)(?P<rest>[^\n]*)"
    r"|[@\\](?P<brace>[{}])"
)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan_header(text: str, path: str = "<stdin>") -> HeaderInfo:
    """Extract group declarations and their lexical scopes from a header."""
    info = HeaderInfo(path=path)
    # Entries are (group_name, first_line_of_scope).
    stack: list[tuple[str, int]] = []

    for block in _BLOCK_RE.finditer(text):
        body = block.group(1)
        body_start = block.start(1)

        # Name a following @{ should open a scope for, and the group that
        # @since / @version / @ingroup currently apply to. Both are scoped to
        # the block, since tags never bind across comments.
        pending: str | None = None
        current: Group | None = None

        for token in _TOKEN_RE.finditer(body):
            line = _line_of(text, body_start + token.start())
            tag, arg, brace = token.group("tag"), token.group("arg"), token.group("brace")

            if tag == "defgroup":
                group = Group(
                    name=arg,
                    title=token.group("rest").strip(),
                    file=path,
                    line=line,
                    # Whatever scope is open at this point encloses the
                    # declaration, so it is a candidate parent to inherit from.
                    lexical_parent=stack[-1][0] if stack else None,
                )
                # A group may be declared once and reopened elsewhere; prefer
                # whichever declaration actually carries the version metadata.
                existing = info.groups.get(arg)
                if existing is None or (existing.version_raw is None and group.version_raw):
                    info.groups[arg] = group
                pending, current = arg, info.groups[arg]
            elif tag == "addtogroup":
                # Reopens a group without declaring it. Tags in this block do
                # still apply, but only to a group this file declared: a group
                # owned by another header must not be invented here, and must
                # never inherit tags meant for the previous @defgroup.
                pending, current = arg, info.groups.get(arg)
            elif current is not None:
                if tag == "since":
                    current.since = arg
                elif tag == "version":
                    current.version_raw = arg
                elif tag == "ingroup":
                    current.parent = arg
            if brace == "{":
                # A bare @{ (as used by @name member groups) nests inside
                # whatever group is already open.
                name = pending or (stack[-1][0] if stack else None)
                if name is not None:
                    stack.append((name, line))
                pending = None
            elif brace == "}" and stack:
                name, start = stack.pop()
                info.scopes.append(Scope(group=name, start=start, end=line))

    # Tolerate headers that never close a scope rather than failing outright.
    total_lines = text.count("\n") + 1
    for name, start in stack:
        info.scopes.append(Scope(group=name, start=start, end=total_lines))

    return info


def scan_file(path, display_path: str | None = None) -> HeaderInfo:
    """Scan a header from disk."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return scan_header(handle.read(), display_path or str(path))


@dataclass(frozen=True)
class Resolution:
    """The version that applies to a group, and where it came from."""

    version: Version | None
    #: Group the version was declared on. None when nothing declares one; equal
    #: to the group's own name when it declares its own.
    source: str | None
    #: Groups walked to reach the source, nearest first.
    chain: tuple[str, ...] = ()

    @property
    def inherited(self) -> bool:
        return bool(self.chain)

    @property
    def lifecycle(self) -> Lifecycle:
        return self.version.lifecycle if self.version else Lifecycle.UNVERSIONED


class GroupIndex:
    """Groups from many headers, with inheritance resolved across them.

    A group nested inside a versioned group is covered by that version: the
    subgroups of a stable API are part of the same API and carry the same
    promise. Since @ingroup routinely names a parent declared in a different
    header, resolving that requires an index over the whole tree rather than
    one file.
    """

    def __init__(self) -> None:
        self.groups: dict[str, Group] = {}

    def add(self, info: HeaderInfo) -> None:
        for group in info.groups.values():
            existing = self.groups.get(group.name)
            # A group reopened in several headers is declared once; keep
            # whichever declaration carries the metadata.
            if existing is None or (existing.version_raw is None and group.version_raw):
                self.groups[group.name] = group

    def add_all(self, infos) -> None:
        for info in infos:
            self.add(info)

    def resolve(self, name: str) -> Resolution:
        """Return the version that applies to a group, inherited if needed."""
        group = self.groups.get(name)
        if group is None:
            return Resolution(None, None)
        if group.version is not None:
            return Resolution(group.version, name)

        # Walk up until a parent declares a version. The visited set guards
        # against @ingroup cycles, which nothing prevents authors from writing.
        chain: list[str] = []
        visited = {name}
        current = group.effective_parent
        while current and current not in visited:
            visited.add(current)
            chain.append(current)
            parent = self.groups.get(current)
            if parent is None:
                break
            if parent.version is not None:
                return Resolution(parent.version, current, tuple(chain))
            current = parent.effective_parent

        return Resolution(None, None, ())

    def lifecycle(self, name: str) -> Lifecycle:
        return self.resolve(name).lifecycle


def index_headers(repo, paths) -> GroupIndex:
    """Build an index from a set of header paths."""
    from pathlib import Path

    index = GroupIndex()
    for path in paths:
        full = Path(repo) / path
        if full.exists():
            index.add(scan_file(full, str(path)))
    return index
