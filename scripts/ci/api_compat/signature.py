# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Symbol level comparison of two Doxygen XML trees.

Doxygen output is used rather than a C parser because it already provides the
three things this comparison needs, correlated with each other:

  * normalized declarations, including struct members in declaration order,
  * group membership resolved through @addtogroup / @{ ... @} nesting,
  * the @since and @version tags that carry the lifecycle state.

Reproducing that from raw headers would mean resolving include paths and
Kconfig-dependent preprocessor state, which Doxygen has already done.

Findings are split by how loudly the change fails for a downstream user:

  loud    the build breaks. Bad, but self-announcing.
  silent  the code still compiles and behaves differently. Far more dangerous,
          and the main reason a textual diff is not sufficient.

Produce the two inputs with a Doxygen run per revision, for example via
git worktree, then point this module at the resulting xml directories.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import doxmlparser
from doxmlparser.compound import DoxCompoundKind

from .apidoc import Lifecycle, parse_version
from .findings import Finding, Severity


@dataclass(frozen=True)
class Symbol:
    """A comparable public symbol."""

    key: str
    kind: str
    name: str
    group: str | None = None
    #: Return type, underlying type, or "" where not applicable.
    ret: str = ""
    #: Parameter types, with names dropped: renaming a parameter is not a
    #: breaking change, retyping one is.
    params: tuple[str, ...] = ()
    #: Macro body or enumerator value.
    value: str = ""
    #: Position within its struct or enum. Reordering is a silent break.
    ordinal: int = -1
    parent: str | None = None
    #: Macro parameter count; None for object-like macros.
    arity: int | None = None
    #: Declaration site, as recorded by Doxygen.
    file: str | None = None
    line: int | None = None

    @property
    def display(self) -> str:
        return f"{self.parent}::{self.name}" if self.parent else self.name


@dataclass
class GroupStatus:
    name: str
    title: str = ""
    since: str | None = None
    version_raw: str | None = None
    #: Enclosing group, from Doxygen's <innergroup>.
    parent: str | None = None

    @property
    def lifecycle(self) -> Lifecycle:
        """The state this group declares itself, ignoring any parent."""
        version = parse_version(self.version_raw)
        return version.lifecycle if version else Lifecycle.UNVERSIONED


@dataclass
class ApiSnapshot:
    symbols: dict[str, Symbol] = field(default_factory=dict)
    groups: dict[str, GroupStatus] = field(default_factory=dict)

    def effective_lifecycle(self, name: str | None) -> tuple[Lifecycle, str | None]:
        """The state that applies to a group, inherited from a parent if needed.

        A subgroup of a stable API is part of that API and carries the same
        promise, so a group with no @version of its own takes the nearest
        versioned ancestor's. Returns the state and, when inherited, the group
        it came from.
        """
        if not name:
            return (Lifecycle.UNVERSIONED, None)

        status = self.groups.get(name)
        if status is None:
            return (Lifecycle.UNVERSIONED, None)
        if status.lifecycle is not Lifecycle.UNVERSIONED:
            return (status.lifecycle, None)

        # Walk up to the nearest ancestor that declares a version. The visited
        # set guards against cycles, which nothing stops an author writing.
        visited = {name}
        current = status.parent
        while current and current not in visited:
            visited.add(current)
            parent = self.groups.get(current)
            if parent is None:
                break
            if parent.lifecycle is not Lifecycle.UNVERSIONED:
                return (parent.lifecycle, current)
            current = parent.parent

        return (Lifecycle.UNVERSIONED, None)


def _text(node) -> str:
    """Flatten a Doxygen linkedTextType into a comparable string."""
    if node is None:
        return ""
    parts: list[str] = []
    for item in getattr(node, "content_", None) or []:
        value = getattr(item, "value", None)
        if isinstance(value, str):
            parts.append(value)
        elif value is not None:
            getter = getattr(value, "get_valueOf_", None)
            parts.append(getter() if getter else str(value))
    return _normalize("".join(parts))


def _normalize(text: str) -> str:
    """Collapse formatting differences that carry no meaning.

    Pointer and comma spacing is normalized so that reflowing a declaration
    does not read as a signature change.
    """
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*\*\s*", "*", text)
    text = re.sub(r"\s*,\s*", ",", text)
    return text


#: Spellings Doxygen may or may not resolve depending on the order in which it
#: happened to preprocess the tree. They mean the same thing to a caller, so
#: comparing them literally produces findings for files that never changed.
_EQUIVALENT_TYPES = {
    "_Bool": "bool",
}

#: Storage class, linkage and attribute decorations that Doxygen sometimes
#: leaves unexpanded in a type. None of them are part of the contract a caller
#: depends on, so they are dropped before comparison. Extend as needed: a macro
#: missing here shows up as a spurious "return type changed" finding.
#:
#: Qualifiers that a caller *can* observe, above all const and volatile, must
#: never be listed here.
_DECORATIONS = frozenset(
    {
        "static",
        "inline",
        "extern",
        "ALWAYS_INLINE",
        "DSP_FUNC_SCOPE",
        "FUNC_NORETURN",
        "__syscall",
        "__syscall_always_inline",
        "__deprecated",
        "__must_check",
        "__pinned_func",
        "__boot_func",
        "__isr",
    }
)

_TOKEN_SPLIT_RE = re.compile(r"(\w+)")


def _normalize_type(text: str) -> str:
    """Reduce a rendered type to the part a caller actually depends on.

    Doxygen's macro expansion is not stable across runs: the same declaration
    can render as "DSP_FUNC_SCOPE void" in one snapshot and "void" in the next,
    or as "_Bool" rather than "bool". Without this, comparing two snapshots of
    an unmodified tree reports changes.
    """
    if not text:
        return text

    def substitute(match: re.Match) -> str:
        word = match.group(1)
        if word in _DECORATIONS:
            return ""
        return _EQUIVALENT_TYPES.get(word, word)

    return _normalize(_TOKEN_SPLIT_RE.sub(substitute, text))


def _type_text(node) -> str:
    """Flatten a node as a type, with unstable decorations removed."""
    return _normalize_type(_text(node))


def _params_of(memberdef) -> tuple[str, ...]:
    types = []
    for param in memberdef.get_param():
        rendered = _type_text(param.get_type())
        # "void" as the sole parameter means "no parameters".
        if rendered and rendered != "void":
            types.append(rendered)
    return tuple(types)


#: Include roots the Zephyr Doxyfile lists in STRIP_FROM_PATH and
#: STRIP_FROM_INC_PATH. Doxygen removes these prefixes from the locations it
#: records, so "include/zephyr/drivers/gpio.h" is reported as
#: "zephyr/drivers/gpio.h". Putting the prefix back matters: a reviewer follows
#: these paths, and GitHub only anchors an annotation to a line when the path
#: it is given actually exists in the repository.
_STRIPPED_ROOTS = (
    "include",
    "kernel/include",
    "lib/libc/minimal/include",
    "subsys/testsuite/include",
    "subsys/testsuite/ztest/include",
    "subsys/secure_storage/include",
    "subsys/secure_storage/include/internal",
)


def _repo_relative(path: str, root: Path) -> str:
    """Undo Doxygen's prefix stripping, so the path names a real file.

    ``root`` is any checkout of the tree; it is only read to test which prefix
    puts the file back. It need not be the revision the snapshot came from,
    which matters because compare-revs discards its worktrees before comparing.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        # Nothing was stripped; make it relative to the tree if we can.
        with contextlib.suppress(ValueError):
            return str(candidate.resolve().relative_to(root))
        return path

    if (root / candidate).exists():
        return path
    for prefix in _STRIPPED_ROOTS:
        if (root / prefix / candidate).exists():
            return str(Path(prefix) / candidate)

    # A header removed by the change under test exists in no checkout, so the
    # probe above cannot place it. Everything under zephyr/ comes from the
    # public include root, which is enough to name it.
    if candidate.parts and candidate.parts[0] == "zephyr":
        return str(Path("include") / candidate)

    # Outside the tree (a module, or a generated header): keep what Doxygen said.
    return path


def _location_of(memberdef, root: Path | None) -> tuple[str | None, int | None]:
    """Return the declaration site, made repo-relative where possible."""
    location = memberdef.get_location()
    if location is None:
        return (None, None)
    path, line = location.get_file(), location.get_line()
    if path and root is not None:
        path = _repo_relative(path, root)
    return (path, int(line) if line else None)


def _group_status(compounddef) -> GroupStatus:
    status = GroupStatus(
        name=compounddef.get_compoundname(),
        title=compounddef.get_title() or "",
    )
    detailed = compounddef.get_detaileddescription()
    if detailed is None:
        return status
    for para in detailed.get_para():
        for sect in para.get_simplesect():
            if not sect.get_para():
                continue
            value = sect.get_para()[0].get_valueOf_().strip()
            if sect.get_kind() == "since":
                status.since = value
            elif sect.get_kind() == "version":
                status.version_raw = value
    return status


def _add_members(
    snapshot: ApiSnapshot,
    compounddef,
    group: str | None,
    root: Path | None = None,
) -> None:
    """Record every memberdef of a compound into the snapshot."""
    for section in compounddef.get_sectiondef():
        for ordinal, member in enumerate(section.get_memberdef()):
            kind = member.get_kind()
            name = member.get_name()
            if not name:
                continue
            where = _location_of(member, root)

            if kind == "enum":
                snapshot.symbols[f"enum:{name}"] = Symbol(
                    key=f"enum:{name}",
                    kind="enum",
                    name=name,
                    group=group,
                    file=where[0],
                    line=where[1],
                )
                # Enumerators are what callers actually depend on.
                for index, enumvalue in enumerate(member.get_enumvalue()):
                    ev_name = enumvalue.get_name()
                    key = f"enumvalue:{ev_name}"
                    snapshot.symbols[key] = Symbol(
                        key=key,
                        kind="enumvalue",
                        name=ev_name,
                        group=group,
                        value=_normalize(_text(enumvalue.get_initializer()).lstrip("= ")),
                        ordinal=index,
                        parent=name,
                        file=where[0],
                        line=where[1],
                    )
                continue

            if kind == "define":
                params = member.get_param()
                key = f"define:{name}"
                snapshot.symbols[key] = Symbol(
                    key=key,
                    kind="define",
                    name=name,
                    group=group,
                    value=_text(member.get_initializer()),
                    arity=len(params) if params else None,
                    file=where[0],
                    line=where[1],
                )
                continue

            if kind == "variable" and compounddef.get_kind() in (
                DoxCompoundKind.STRUCT,
                DoxCompoundKind.UNION,
            ):
                parent = compounddef.get_compoundname()
                key = f"member:{parent}::{name}"
                snapshot.symbols[key] = Symbol(
                    key=key,
                    kind="member",
                    name=name,
                    group=group,
                    ret=_type_text(member.get_type()),
                    ordinal=ordinal,
                    parent=parent,
                    file=where[0],
                    line=where[1],
                )
                continue

            if kind in ("function", "typedef", "variable"):
                key = f"{kind}:{name}"
                snapshot.symbols[key] = Symbol(
                    key=key,
                    kind=kind,
                    name=name,
                    group=group,
                    ret=_type_text(member.get_type()),
                    params=_params_of(member),
                    # A typedef's parameter list lives in argsstring.
                    value=_normalize_type(member.get_argsstring() or "")
                    if kind == "typedef"
                    else "",
                    file=where[0],
                    line=where[1],
                )


def load_snapshot(xml_dir: Path, root: Path | None = None) -> ApiSnapshot:
    """Build a symbol table from a Doxygen XML output directory.

    If root is given, declaration paths are reported relative to it.
    """
    xml_dir = Path(xml_dir)
    root = Path(root).resolve() if root else None
    index_path = xml_dir / "index.xml"
    if not index_path.exists():
        raise FileNotFoundError(f"no Doxygen index at {index_path}")

    snapshot = ApiSnapshot()
    index = doxmlparser.index.parse(str(index_path), True)

    groups, others = [], []
    for compound in index.get_compound():
        target = (
            groups
            if compound.get_kind() == DoxCompoundKind.GROUP
            else others
            if compound.get_kind() in (DoxCompoundKind.STRUCT, DoxCompoundKind.UNION)
            else None
        )
        if target is not None:
            target.append(compound)

    # Groups first, so that structs can inherit the group that lists them.
    refid_to_group: dict[str, str] = {}
    #: child group refid -> parent group name, from <innergroup>.
    parent_refids: dict[str, str] = {}
    group_names: dict[str, str] = {}

    for compound in groups:
        path = xml_dir / f"{compound.get_refid()}.xml"
        if not path.exists():
            continue
        for compounddef in doxmlparser.compound.parse(str(path), True).get_compounddef():
            status = _group_status(compounddef)
            snapshot.groups[status.name] = status
            group_names[compounddef.get_id()] = status.name
            for inner in compounddef.get_innerclass():
                refid_to_group[inner.get_refid()] = status.name
            for inner in compounddef.get_innergroup():
                parent_refids[inner.get_refid()] = status.name
            _add_members(snapshot, compounddef, status.name, root)

    # <innergroup> is Doxygen's own resolution of the group tree, so use it
    # rather than re-deriving nesting from the headers.
    for refid, parent in parent_refids.items():
        name = group_names.get(refid)
        if name and name in snapshot.groups:
            snapshot.groups[name].parent = parent

    for compound in others:
        path = xml_dir / f"{compound.get_refid()}.xml"
        if not path.exists():
            continue
        group = refid_to_group.get(compound.get_refid())
        for compounddef in doxmlparser.compound.parse(str(path), True).get_compounddef():
            _add_members(snapshot, compounddef, group, root)

    return snapshot


@dataclass
class Change:
    """One difference between a base and head symbol."""

    title: str
    detail: str
    silent: bool


def _diff_symbol(base: Symbol, head: Symbol) -> list[Change]:
    changes: list[Change] = []
    what = f"{base.kind} '{base.display}'"

    if base.kind in ("function", "variable"):
        if base.ret != head.ret:
            changes.append(
                Change(
                    f"return type of {what} changed",
                    f"'{base.ret}' became '{head.ret}'.",
                    silent=True,
                )
            )
        if len(base.params) != len(head.params):
            changes.append(
                Change(
                    f"parameter count of {what} changed",
                    f"({', '.join(base.params)}) became ({', '.join(head.params)}).\n"
                    "Every existing caller fails to compile.",
                    silent=False,
                )
            )
        else:
            for index, (before, after) in enumerate(zip(base.params, head.params, strict=True)):
                if before != after:
                    changes.append(
                        Change(
                            f"parameter {index + 1} of {what} changed type",
                            f"'{before}' became '{after}'.\n"
                            "Callers may still compile and silently convert.",
                            silent=True,
                        )
                    )

    elif base.kind == "typedef":
        if base.ret != head.ret or base.value != head.value:
            changes.append(
                Change(
                    f"{what} changed signature",
                    f"'{base.ret}{base.value}' became '{head.ret}{head.value}'.\n"
                    "Existing implementations of this callback may no longer match.",
                    silent=True,
                )
            )

    elif base.kind == "define":
        if base.arity != head.arity:
            changes.append(
                Change(
                    f"{what} changed arity",
                    f"took {base.arity} argument(s), now takes {head.arity}.",
                    silent=False,
                )
            )
        elif base.value != head.value:
            changes.append(
                Change(
                    f"{what} changed value",
                    f"'{base.value}' became '{head.value}'.\n"
                    "Callers keep compiling and silently get the new value.",
                    silent=True,
                )
            )

    elif base.kind == "enumvalue":
        if base.value != head.value:
            changes.append(
                Change(
                    f"enumerator '{base.name}' changed value",
                    f"'{base.value or base.ordinal}' became '{head.value or head.ordinal}'.",
                    silent=True,
                )
            )
        elif not base.value and base.ordinal != head.ordinal:
            changes.append(
                Change(
                    f"enumerator '{base.name}' was renumbered",
                    f"position {base.ordinal} became {head.ordinal} and the enum "
                    "has no explicit values,\nso its numeric value changed.",
                    silent=True,
                )
            )

    elif base.kind == "member":
        if base.ret != head.ret:
            changes.append(
                Change(
                    f"field '{base.display}' changed type",
                    f"'{base.ret}' became '{head.ret}'.",
                    silent=True,
                )
            )
        if base.ordinal != head.ordinal:
            changes.append(
                Change(
                    f"field '{base.display}' was reordered",
                    f"position {base.ordinal} became {head.ordinal}.\n"
                    "Positional initializers keep compiling and assign the wrong "
                    "field.",
                    silent=True,
                )
            )

    return changes


def _removal_detail(effective: Lifecycle, declared: Lifecycle) -> str:
    """Explain what a removal means for the lifecycle state that applies.

    api_lifecycle.rst only imposes a deprecation period on stable APIs;
    experimental and unstable ones may be removed outright. Saying otherwise
    would contradict the severity assigned to the same finding.
    """
    gone = "The symbol is gone from the public API.\n"

    if effective is Lifecycle.EXPERIMENTAL:
        return gone + (
            "This API is experimental, so it may be removed at any time and no\n"
            "deprecation period is required. Reported for visibility only."
        )

    if effective is Lifecycle.UNSTABLE:
        return gone + (
            "This API is unstable, so it may be removed without a deprecation\n"
            "period, and such changes are not announced. Downstream users will\n"
            "nonetheless discover it at build time."
        )

    detail = gone + (
        "Removing a stable symbol requires a deprecation period of at least\n"
        "two releases; see doc/develop/api/api_lifecycle.rst."
    )
    if declared is Lifecycle.UNVERSIONED:
        detail += (
            "\nThe owning group declares no @version and is therefore treated as\n"
            "stable; pass --unversioned-is to change that assumption."
        )
    return detail


def _severity(lifecycle: Lifecycle, unversioned_is: Lifecycle) -> Severity:
    if lifecycle is Lifecycle.UNVERSIONED:
        lifecycle = unversioned_is
    if lifecycle is Lifecycle.STABLE:
        return Severity.ERROR
    if lifecycle is Lifecycle.UNSTABLE:
        return Severity.WARNING
    return Severity.NOTE


def compare(
    base: ApiSnapshot,
    head: ApiSnapshot,
    unversioned_is: Lifecycle = Lifecycle.STABLE,
) -> list[Finding]:
    """Compare two snapshots and report contract-relevant differences."""
    findings: list[Finding] = []
    check = "signature"

    for key, base_symbol in sorted(base.symbols.items()):
        # A group with no @version of its own is covered by the nearest
        # versioned ancestor: a subgroup of a stable API is part of that API.
        lifecycle, inherited_from = base.effective_lifecycle(base_symbol.group)
        # Still unversioned after that walk means policy decides, but the
        # finding keeps reporting the state the tree actually declares.
        effective = unversioned_is if lifecycle is Lifecycle.UNVERSIONED else lifecycle
        severity = _severity(lifecycle, unversioned_is)
        # lifecycle stays one of the four state names: the HTML report uses it
        # as a filter facet, so the inheritance note belongs in the detail.
        inherit_note = (
            f"\n'{base_symbol.group}' declares no @version of its own and is "
            f"covered by '{inherited_from}'."
            if inherited_from
            else ""
        )
        common = {
            "check": check,
            "symbol": base_symbol.display,
            "group": base_symbol.group,
            "lifecycle": lifecycle.value,
            # Locate against the base revision: for a removed symbol the head
            # has no location to point at.
            "file": base_symbol.file,
            "line": base_symbol.line,
        }

        head_symbol = head.symbols.get(key)
        if head_symbol is not None and head_symbol.file:
            # Point at where the symbol is now, so a review annotation lands on
            # the line the change actually touched. A removed symbol has no
            # such line, and keeps the location it had in the base revision.
            common["file"] = head_symbol.file
            common["line"] = head_symbol.line
        if head_symbol is None:
            # A struct member vanishing with its whole struct is reported once,
            # via the struct's other members; that redundancy is acceptable.
            findings.append(
                Finding(
                    severity=severity,
                    title=f"{base_symbol.kind} '{base_symbol.display}' was removed",
                    detail=_removal_detail(effective, lifecycle) + inherit_note,
                    **common,
                )
            )
            continue

        for change in _diff_symbol(base_symbol, head_symbol):
            prefix = "silently changes behavior: " if change.silent else ""
            findings.append(
                Finding(
                    severity=severity,
                    title=prefix + change.title,
                    detail=change.detail + inherit_note,
                    **common,
                )
            )

    return findings


def limit_to_files(findings: list[Finding], allowed: set[str]) -> list[Finding]:
    """Drop findings whose declaration site the change never touched.

    Doxygen's macro expansion is not reproducible across runs: a header full of
    generated declarations, such as one built out of Fake Function Framework
    macros, can expand in one snapshot and not in the next, and every symbol it
    generates then looks removed. Those phantom findings always land in files
    the change did not touch, so intersecting with the change's own paths
    removes them.

    The cost is a blind spot: a change that alters a macro in one header can
    legitimately change what another header expands to, and that would now go
    unreported. That is rarer than the noise it removes.
    """
    return [f for f in findings if f.file and f.file in allowed]


def compare_xml_dirs(
    base_xml: Path,
    head_xml: Path,
    unversioned_is: Lifecycle = Lifecycle.STABLE,
    base_root: Path | None = None,
    head_root: Path | None = None,
) -> list[Finding]:
    return compare(
        load_snapshot(base_xml, base_root),
        load_snapshot(head_xml, head_root),
        unversioned_is,
    )
