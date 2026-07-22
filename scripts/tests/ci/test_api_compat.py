#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/ci/api_compat.

Run from the zephyr root::

    pytest scripts/tests/ci/test_api_compat.py -v
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ZEPHYR_BASE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ZEPHYR_BASE / "scripts" / "ci"))

from api_compat import apidoc, checks  # noqa: E402
from api_compat.apidoc import Lifecycle, parse_version, scan_header  # noqa: E402
from api_compat.findings import Severity  # noqa: E402


class TestVersionParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.0.0", Lifecycle.STABLE),
            ("2.3.1", Lifecycle.STABLE),
            ("0.1.0", Lifecycle.EXPERIMENTAL),
            ("0.1.1", Lifecycle.EXPERIMENTAL),
            ("0.0.1", Lifecycle.EXPERIMENTAL),
            ("0.2.0", Lifecycle.UNSTABLE),
            ("0.8.0", Lifecycle.UNSTABLE),
            ("0.10.0", Lifecycle.UNSTABLE),
        ],
    )
    def test_lifecycle_mapping(self, raw, expected):
        assert parse_version(raw).lifecycle is expected

    @pytest.mark.parametrize("raw", ["", None, "abc", "1", "1.x.0", "v1.0.0"])
    def test_rejects_non_semver(self, raw):
        assert parse_version(raw) is None

    def test_two_component_version_is_accepted(self):
        assert parse_version("1.2") == apidoc.Version(1, 2, 0, "1.2")

    def test_zero_zero_is_flagged_as_offspec(self):
        assert parse_version("0.0.1").is_wellformed is False
        assert parse_version("0.1.0").is_wellformed is True

    def test_unversioned_is_protected(self):
        # Most of the tree is unversioned; failing closed is deliberate.
        assert Lifecycle.UNVERSIONED.is_protected
        assert Lifecycle.STABLE.is_protected
        assert not Lifecycle.EXPERIMENTAL.is_protected


class TestScanHeader:
    def test_extracts_since_and_version(self):
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @defgroup foo_interface Foo
                 * @since 3.7
                 * @version 0.1.0
                 * @ingroup io_interfaces
                 * @{
                 */
                int foo_do(void);
                /** @} */
            """)
        )
        group = info.groups["foo_interface"]
        assert (group.since, group.version_raw) == ("3.7", "0.1.0")
        assert group.title == "Foo"
        assert group.parent == "io_interfaces"
        assert group.lifecycle is Lifecycle.EXPERIMENTAL

    def test_two_groups_in_one_block(self):
        """The shape used by drivers/gpio.h: nested groups in one comment."""
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @defgroup outer Outer
                 * @since 1.0
                 * @version 1.0.0
                 * @{
                 *
                 * @defgroup inner Inner
                 *
                 * @{
                 * @}
                 */
                int outer_call(void);
                /** @} */
            """)
        )
        assert set(info.groups) == {"outer", "inner"}
        # @since/@version must bind to outer, not leak into inner.
        assert info.groups["outer"].version_raw == "1.0.0"
        assert info.groups["inner"].version_raw is None
        assert all(s.end >= s.start for s in info.scopes)

    def test_symbol_resolves_to_enclosing_group(self):
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @defgroup outer Outer
                 * @version 1.0.0
                 * @{
                 */
                int outer_call(void);

                /**
                 * @defgroup nested Nested
                 * @version 0.1.0
                 * @{
                 */
                int nested_call(void);
                /** @} */

                int outer_tail(void);
                /** @} */
            """)
        )
        lines = {"outer_call": 7, "nested_call": 14, "outer_tail": 17}
        assert info.group_at(lines["outer_call"]) == "outer"
        # The innermost enclosing group wins.
        assert info.group_at(lines["nested_call"]) == "nested"
        assert info.group_at(lines["outer_tail"]) == "outer"

    def test_name_member_group_does_not_steal_scope(self):
        """@name ... @{ nests inside the current group rather than replacing it."""
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @defgroup outer Outer
                 * @version 1.0.0
                 * @{
                 */

                /**
                 * @name Flags
                 * @{
                 */
                #define FLAG_A 1
                /** @} */

                /** @} */
            """)
        )
        assert info.group_at(12) == "outer"

    def test_addtogroup_reopens_without_declaring(self):
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @addtogroup existing
                 * @{
                 */
                int call(void);
                /** @} */
            """)
        )
        assert "existing" not in info.groups
        assert info.group_at(5) == "existing"

    def test_addtogroup_updates_a_group_declared_in_the_same_file(self):
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @defgroup thing Thing
                 * @{
                 */
                /** @} */

                /**
                 * @addtogroup thing
                 * @since 4.1
                 * @version 0.8.0
                 * @{
                 */
                int thing_call(void);
                /** @} */
            """)
        )
        assert info.groups["thing"].version_raw == "0.8.0"
        assert info.groups["thing"].lifecycle is Lifecycle.UNSTABLE

    def test_addtogroup_does_not_misattribute_tags(self):
        """Tags after @addtogroup must not land on the previous @defgroup."""
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @defgroup owned Owned
                 * @since 1.0
                 * @version 1.0.0
                 * @{
                 */
                int owned_call(void);
                /** @} */

                /**
                 * @addtogroup elsewhere
                 * @since 9.9
                 * @version 0.1.0
                 * @{
                 */
                int other_call(void);
                /** @} */
            """)
        )
        # The foreign group is not invented ...
        assert "elsewhere" not in info.groups
        # ... and its tags do not overwrite the group declared here.
        assert info.groups["owned"].since == "1.0"
        assert info.groups["owned"].version_raw == "1.0.0"

    def test_unclosed_scope_is_tolerated(self):
        info = scan_header(
            textwrap.dedent("""
                /**
                 * @defgroup leaky Leaky
                 * @version 1.0.0
                 * @{
                 */
                int leaky_call(void);
            """)
        )
        assert info.group_at(6) == "leaky"

    def test_trailing_member_comment_is_ignored(self):
        # /**< blocks never carry group tags and must not open scopes.
        info = scan_header("int field; /**< @{ not a group */\n")
        assert info.scopes == []


HEADER = textwrap.dedent("""\
    /**
     * @defgroup demo_api Demo
     * @since 4.0
     * @version {version}
     * @{{
     */
    int demo_call(void);
    {extra}
    /** @}} */
    """)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repository with one versioned API header."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")

    header = tmp_path / "include" / "zephyr" / "demo.h"
    header.parent.mkdir(parents=True)
    header.write_text(HEADER.format(version="1.0.0", extra=""))
    _git(tmp_path, "add", "include/zephyr/demo.h")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def _commit(repo: Path, path: str, text: str) -> None:
    (repo / path).write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", "change")


class TestGroupMetadataCheck:
    def test_new_group_without_metadata_is_an_error(self, repo):
        _commit(
            repo,
            "include/zephyr/new.h",
            "/**\n * @defgroup bare Bare\n * @{\n */\nint f(void);\n/** @} */\n",
        )
        found = checks.check_group_metadata("HEAD~1..HEAD", repo)
        titles = " ".join(f.title for f in found)
        assert "no @version" in titles
        assert "no @since" in titles
        assert all(f.severity is Severity.ERROR for f in found)

    def test_complete_new_group_passes(self, repo):
        _commit(
            repo,
            "include/zephyr/new.h",
            HEADER.format(version="0.1.0", extra="").replace("demo_api", "new_api"),
        )
        assert checks.check_group_metadata("HEAD~1..HEAD", repo) == []

    def test_untouched_groups_are_not_reported(self, repo):
        # The pre-existing header has metadata, but an unrelated edit to a
        # different file must not drag every group in the tree into the report.
        _commit(repo, "include/zephyr/other.h", "int unrelated(void);\n")
        assert checks.check_group_metadata("HEAD~1..HEAD", repo) == []

    def test_malformed_version_is_reported(self, repo):
        _commit(
            repo,
            "include/zephyr/new.h",
            HEADER.format(version="experimental", extra="").replace("demo_api", "new_api"),
        )
        found = checks.check_group_metadata("HEAD~1..HEAD", repo)
        assert any("malformed @version" in f.title for f in found)


class TestDeprecationCheck:
    def test_deprecation_without_bump_is_an_error(self, repo):
        _commit(
            repo,
            "include/zephyr/demo.h",
            HEADER.format(version="1.0.0", extra="__deprecated int demo_old(void);"),
        )
        found = checks.check_deprecation("HEAD~1..HEAD", repo)
        assert len(found) == 1
        assert found[0].severity is Severity.ERROR
        assert "without a minor version bump" in found[0].title
        assert found[0].group == "demo_api"

    def test_deprecation_with_bump_passes(self, repo):
        _commit(
            repo,
            "include/zephyr/demo.h",
            HEADER.format(version="1.1.0", extra="__deprecated int demo_old(void);"),
        )
        assert checks.check_deprecation("HEAD~1..HEAD", repo) == []

    def test_experimental_api_only_warns(self, repo):
        _commit(repo, "include/zephyr/demo.h", HEADER.format(version="0.1.0", extra=""))
        _commit(
            repo,
            "include/zephyr/demo.h",
            HEADER.format(version="0.1.0", extra="__deprecated int demo_old(void);"),
        )
        found = checks.check_deprecation("HEAD~1..HEAD", repo)
        assert len(found) == 1
        assert found[0].severity is Severity.WARNING

    def test_deprecated_version_macro_is_recognized(self, repo):
        # __deprecated_version() must not be missed by the __deprecated regex.
        _commit(
            repo,
            "include/zephyr/demo.h",
            HEADER.format(version="1.0.0", extra="__deprecated_version(4.5) int demo_old(void);"),
        )
        assert len(checks.check_deprecation("HEAD~1..HEAD", repo)) == 1

    def test_unrelated_change_is_quiet(self, repo):
        _commit(
            repo,
            "include/zephyr/demo.h",
            HEADER.format(version="1.0.0", extra="int demo_new(void);"),
        )
        assert checks.check_deprecation("HEAD~1..HEAD", repo) == []


pytest.importorskip("doxmlparser", reason="doxmlparser is needed for signature comparison")

from api_compat.signature import (  # noqa: E402
    ApiSnapshot,
    GroupStatus,
    Symbol,
    _normalize_type,
    compare,
)


class TestTypeNormalization:
    """Doxygen expands macros inconsistently between runs.

    Comparing two snapshots of an unmodified tree must produce nothing; these
    are the exact spellings that were observed to differ in practice.
    """

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("_Bool", "bool"),
            ("DSP_FUNC_SCOPE void", "void"),
            ("static inline int", "int"),
            ("__syscall int", "int"),
            ("FUNC_NORETURN void", "void"),
            ("const char *", "const char*"),
            ("int  *", "int*"),
        ],
    )
    def test_equivalent_spellings_compare_equal(self, left, right):
        assert _normalize_type(left) == _normalize_type(right)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            # Qualifiers a caller can observe must survive normalization.
            ("const char*", "char*"),
            ("volatile int", "int"),
            ("uint8_t", "int8_t"),
            ("uint32_t", "uint64_t"),
            ("struct foo*", "struct bar*"),
            ("int*", "int**"),
        ],
    )
    def test_meaningful_differences_survive(self, left, right):
        assert _normalize_type(left) != _normalize_type(right)

    def test_empty_type_is_untouched(self):
        assert _normalize_type("") == ""


def _snapshot(version: str, *symbols: Symbol) -> ApiSnapshot:
    snapshot = ApiSnapshot(groups={"g": GroupStatus(name="g", version_raw=version)})
    snapshot.symbols = {s.key: s for s in symbols}
    return snapshot


def _fn(name: str, ret: str = "int", params: tuple[str, ...] = ()) -> Symbol:
    return Symbol(
        key=f"function:{name}", kind="function", name=name, group="g", ret=ret, params=params
    )


class TestSignatureComparison:
    def test_removal_of_stable_symbol_is_an_error(self):
        base = _snapshot("1.0.0", _fn("gone"))
        found = compare(base, _snapshot("1.0.0"))
        assert len(found) == 1
        assert found[0].severity is Severity.ERROR
        assert "was removed" in found[0].title

    def test_parameter_count_change_is_loud(self):
        base = _snapshot("1.0.0", _fn("f", params=("int",)))
        head = _snapshot("1.0.0", _fn("f", params=("int", "int")))
        found = compare(base, head)
        assert "parameter count" in found[0].title
        # A build break is not labelled as a silent behaviour change.
        assert "silently" not in found[0].title

    def test_parameter_type_change_is_marked_silent(self):
        base = _snapshot("1.0.0", _fn("f", params=("uint8_t",)))
        head = _snapshot("1.0.0", _fn("f", params=("int8_t",)))
        found = compare(base, head)
        assert found[0].title.startswith("silently changes behavior:")

    def test_macro_value_change_is_silent(self):
        define = Symbol(key="define:X", kind="define", name="X", group="g", value="1")
        changed = Symbol(key="define:X", kind="define", name="X", group="g", value="2")
        found = compare(_snapshot("1.0.0", define), _snapshot("1.0.0", changed))
        assert "changed value" in found[0].title
        assert found[0].title.startswith("silently")

    def test_struct_field_reorder_is_reported(self):
        def field(name, ordinal):
            return Symbol(
                key=f"member:s::{name}",
                kind="member",
                name=name,
                group="g",
                ret="int",
                ordinal=ordinal,
                parent="s",
            )

        found = compare(
            _snapshot("1.0.0", field("a", 0), field("b", 1)),
            _snapshot("1.0.0", field("a", 1), field("b", 0)),
        )
        assert len(found) == 2
        assert all("reordered" in f.title for f in found)

    def test_enumerator_renumbering_is_reported(self):
        def value(name, ordinal):
            return Symbol(
                key=f"enumvalue:{name}",
                kind="enumvalue",
                name=name,
                group="g",
                value="",
                ordinal=ordinal,
                parent="e",
            )

        found = compare(_snapshot("1.0.0", value("B", 1)), _snapshot("1.0.0", value("B", 2)))
        assert "renumbered" in found[0].title

    def test_experimental_api_is_only_a_note(self):
        base = _snapshot("0.1.0", _fn("f", params=("int",)))
        head = _snapshot("0.1.0", _fn("f", params=("int", "int")))
        assert compare(base, head)[0].severity is Severity.NOTE

    def test_unstable_api_is_a_warning(self):
        base = _snapshot("0.8.0", _fn("f", params=("int",)))
        head = _snapshot("0.8.0", _fn("f", params=("int", "int")))
        assert compare(base, head)[0].severity is Severity.WARNING

    def test_unversioned_policy_is_configurable(self):
        base = _snapshot(None, _fn("f", params=("int",)))
        head = _snapshot(None, _fn("f", params=("int", "int")))
        assert compare(base, head)[0].severity is Severity.ERROR
        relaxed = compare(base, head, unversioned_is=Lifecycle.EXPERIMENTAL)
        assert relaxed[0].severity is Severity.NOTE

    def test_added_symbols_are_not_reported(self):
        found = compare(_snapshot("1.0.0"), _snapshot("1.0.0", _fn("brand_new")))
        assert found == []

    def test_identical_snapshots_are_clean(self):
        base = _snapshot("1.0.0", _fn("f", params=("int",)))
        head = _snapshot("1.0.0", _fn("f", params=("int",)))
        assert compare(base, head) == []
