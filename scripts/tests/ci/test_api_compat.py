#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/ci/api_compat.

Run from the zephyr root::

    pytest scripts/tests/ci/test_api_compat.py -v
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from html.parser import HTMLParser
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


class TestVersionInheritance:
    """A group inside a versioned group is part of that API.

    Its subgroups carry the same promise, so a group with no @version of its
    own takes the nearest versioned ancestor's.
    """

    def _index(self, *sources):
        from api_compat.apidoc import GroupIndex

        index = GroupIndex()
        for number, source in enumerate(sources):
            index.add(scan_header(textwrap.dedent(source), f"h{number}.h"))
        return index

    def test_child_inherits_through_ingroup(self):
        index = self._index("""
            /**
             * @defgroup parent Parent
             * @version 1.0.0
             * @{
             */
            /** @} */
            /**
             * @defgroup child Child
             * @ingroup parent
             * @{
             */
            /** @} */
        """)
        resolution = index.resolve("child")
        assert resolution.lifecycle is Lifecycle.STABLE
        assert resolution.source == "parent"
        assert resolution.inherited

    def test_child_inherits_across_headers(self):
        # @ingroup routinely names a parent declared in a different header.
        index = self._index(
            """
            /**
             * @defgroup parent Parent
             * @version 0.8.0
             * @{
             */
            /** @} */
            """,
            """
            /**
             * @defgroup child Child
             * @ingroup parent
             * @{
             */
            /** @} */
            """,
        )
        assert index.resolve("child").lifecycle is Lifecycle.UNSTABLE

    def test_child_inherits_through_lexical_nesting(self):
        # The gpio.h shape: a subgroup declared inside the parent's @{ ... @}
        # with no @ingroup of its own.
        index = self._index("""
            /**
             * @defgroup outer Outer
             * @version 1.0.0
             * @{
             *
             * @defgroup inner Inner
             *
             * @{
             * @}
             */
            /** @} */
        """)
        assert index.resolve("inner").lifecycle is Lifecycle.STABLE

    def test_own_version_wins_over_parent(self):
        index = self._index("""
            /**
             * @defgroup parent Parent
             * @version 1.0.0
             * @{
             */
            /** @} */
            /**
             * @defgroup child Child
             * @ingroup parent
             * @version 0.1.0
             * @{
             */
            /** @} */
        """)
        resolution = index.resolve("child")
        assert resolution.lifecycle is Lifecycle.EXPERIMENTAL
        assert not resolution.inherited

    def test_inheritance_walks_past_unversioned_ancestors(self):
        index = self._index("""
            /**
             * @defgroup top Top
             * @version 1.0.0
             * @{
             */
            /** @} */
            /**
             * @defgroup middle Middle
             * @ingroup top
             * @{
             */
            /** @} */
            /**
             * @defgroup leaf Leaf
             * @ingroup middle
             * @{
             */
            /** @} */
        """)
        resolution = index.resolve("leaf")
        assert resolution.lifecycle is Lifecycle.STABLE
        assert resolution.source == "top"
        assert resolution.chain == ("middle", "top")

    def test_ingroup_wins_over_lexical_parent(self):
        index = self._index("""
            /**
             * @defgroup lexical Lexical
             * @version 1.0.0
             * @{
             *
             * @defgroup child Child
             * @ingroup elsewhere
             *
             * @{
             * @}
             */
            /** @} */
            /**
             * @defgroup elsewhere Elsewhere
             * @version 0.1.0
             * @{
             */
            /** @} */
        """)
        # The explicit @ingroup names the real parent, not the enclosing scope.
        assert index.resolve("child").lifecycle is Lifecycle.EXPERIMENTAL

    def test_unversioned_chain_stays_unversioned(self):
        index = self._index("""
            /**
             * @defgroup parent Parent
             * @{
             */
            /** @} */
            /**
             * @defgroup child Child
             * @ingroup parent
             * @{
             */
            /** @} */
        """)
        resolution = index.resolve("child")
        assert resolution.lifecycle is Lifecycle.UNVERSIONED
        assert not resolution.inherited

    def test_cycles_do_not_hang(self):
        # Nothing stops an author writing a loop of @ingroup tags.
        index = self._index("""
            /**
             * @defgroup a A
             * @ingroup b
             * @{
             */
            /** @} */
            /**
             * @defgroup b B
             * @ingroup a
             * @{
             */
            /** @} */
        """)
        assert index.resolve("a").lifecycle is Lifecycle.UNVERSIONED

    def test_unknown_group_resolves_to_nothing(self):
        assert self._index("").resolve("nope").lifecycle is Lifecycle.UNVERSIONED


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

    def test_group_inheriting_a_version_needs_no_tags_of_its_own(self, repo):
        # A subgroup of a versioned API is part of that API.
        _commit(
            repo,
            "include/zephyr/new.h",
            "/**\n * @defgroup child Child\n * @ingroup demo_api\n * @{\n */\n"
            "int f(void);\n/** @} */\n",
        )
        assert checks.check_group_metadata("HEAD~1..HEAD", repo) == []

    def test_group_inheriting_from_an_unversioned_parent_is_still_reported(self, repo):
        _commit(
            repo,
            "include/zephyr/new.h",
            "/**\n * @defgroup orphan Orphan\n * @ingroup nowhere\n * @{\n */\n"
            "int f(void);\n/** @} */\n",
        )
        found = checks.check_group_metadata("HEAD~1..HEAD", repo)
        assert any("no @version" in f.title for f in found)

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


from api_compat.history import Age, Release, age_of  # noqa: E402
from api_compat.propose import (  # noqa: E402
    Evidence,
    Kind,
    classify,
)
from api_compat.report import ReportMeta, format_html  # noqa: E402


def _release(major, minor, stamp):
    return Release(f"v{major}.{minor}.0", major, minor, stamp)


class TestReleaseAge:
    RELEASES = [_release(3, 0, 100), _release(3, 1, 200), _release(3, 2, 300), _release(4, 0, 400)]

    def test_added_before_a_release_counts_from_it(self):
        age = age_of(self.RELEASES, 150)
        assert age.first.short == "3.1"
        assert age.releases == 3

    def test_added_before_everything_counts_all(self):
        assert age_of(self.RELEASES, 1).releases == 4

    def test_added_after_the_last_release_has_not_shipped(self):
        age = age_of(self.RELEASES, 500)
        assert age == Age(None, 0)
        assert not age.shipped

    def test_unknown_add_time_is_not_guessed(self):
        assert age_of(self.RELEASES, None).releases == 0

    def test_exactly_on_a_release_counts_that_release(self):
        assert age_of(self.RELEASES, 300).first.short == "3.2"


def _evidence(kind=Kind.PERIPHERAL, releases=5, impls=3, users=50, version=None):
    group = apidoc.Group(name="g", title="G", file="include/zephyr/x.h", line=1)
    group.version_raw = version
    return Evidence(
        group=group,
        header="include/zephyr/x.h",
        kind=kind,
        releases=releases,
        first_release="3.0",
        implementations=impls,
        users=users,
    )


class TestExtensionHeaders:
    """Per-vendor and emulator headers extend an API; they are not APIs.

    Left in, they collect the SoC files that include them as "users" and get
    proposed stable, which they are not.
    """

    @pytest.mark.parametrize(
        "header",
        [
            "include/zephyr/drivers/clock_control/stm32_clock_control.h",
            "include/zephyr/drivers/clock_control/renesas_ra_cgc.h",
            "include/zephyr/drivers/sensor/ccs811.h",
            "include/zephyr/drivers/emul.h",
            "include/zephyr/drivers/i2c_emul.h",
            "include/zephyr/drivers/emul_sensor.h",
        ],
    )
    def test_extensions_are_recognized(self, header):
        from api_compat.evidence import is_extension_header

        assert is_extension_header(header)

    @pytest.mark.parametrize(
        "header",
        [
            "include/zephyr/drivers/gpio.h",
            "include/zephyr/drivers/uart.h",
            "include/zephyr/drivers/clock_control.h",
            "include/zephyr/net/rtp.h",
            "include/zephyr/sys/util.h",
        ],
    )
    def test_real_apis_are_kept(self, header):
        from api_compat.evidence import is_extension_header

        assert not is_extension_header(header)


class TestClassify:
    """The promotion criteria from doc/develop/api/api_lifecycle.rst."""

    def test_unreleased_is_experimental(self):
        assert classify(_evidence(releases=0)).state is Lifecycle.EXPERIMENTAL

    def test_peripheral_with_one_implementation_is_experimental(self):
        # "at least two implementations on different hardware platforms"
        proposal = classify(_evidence(impls=1, releases=20, users=500))
        assert proposal.state is Lifecycle.EXPERIMENTAL
        assert any("two implementations" in r or "2 implementations" in r for r in proposal.reasons)

    def test_peripheral_with_one_release_is_experimental(self):
        assert classify(_evidence(releases=1, impls=9)).state is Lifecycle.EXPERIMENTAL

    def test_peripheral_meeting_both_bars_is_unstable(self):
        # Two implementations and two releases clears experimental, but few
        # users keeps it short of stable.
        assert classify(_evidence(releases=2, impls=2, users=3)).state is Lifecycle.UNSTABLE

    def test_widely_used_mature_peripheral_is_stable(self):
        proposal = classify(_evidence(releases=11, impls=20, users=400))
        assert proposal.state is Lifecycle.STABLE
        # Stable needs criteria this tool cannot measure.
        assert proposal.needs_review

    def test_niche_peripheral_stays_unstable(self):
        # The w1 shape: long-lived and multi-vendor, but barely consumed.
        assert classify(_evidence(releases=11, impls=2, users=5)).state is Lifecycle.UNSTABLE

    def test_agnostic_api_ignores_implementation_count(self):
        # Classes with no vtable report zero implementations; judging them by
        # that would mark long-stable APIs experimental.
        proposal = classify(_evidence(kind=Kind.AGNOSTIC, releases=20, impls=0, users=600))
        assert proposal.state is Lifecycle.STABLE

    def test_agnostic_api_with_one_release_is_experimental(self):
        assert (
            classify(_evidence(kind=Kind.AGNOSTIC, releases=1, impls=0, users=600)).state
            is Lifecycle.EXPERIMENTAL
        )

    def test_agnostic_api_with_few_users_is_unstable(self):
        assert (
            classify(_evidence(kind=Kind.AGNOSTIC, releases=9, impls=0, users=2)).state
            is Lifecycle.UNSTABLE
        )

    def test_every_proposal_explains_itself(self):
        for evidence in (
            _evidence(releases=0),
            _evidence(impls=1),
            _evidence(releases=11, impls=20, users=400),
            _evidence(kind=Kind.AGNOSTIC, releases=9, users=2),
        ):
            assert classify(evidence).reasons


def _finding(**kwargs):
    from api_compat.findings import Finding

    base = {
        "check": "signature",
        "severity": Severity.ERROR,
        "title": "t",
        "detail": "d",
        "file": "include/zephyr/x.h",
        "line": 1,
    }
    return Finding(**{**base, **kwargs})


class TestGithubAnnotations:
    """GitHub parses "::" and "," as annotation syntax, not as text."""

    def test_colons_in_a_title_are_escaped(self):
        from api_compat.findings import format_github

        line = format_github(
            [_finding(title="member 'led_fake::arg0' was removed", group="led_fake")]
        )
        # Exactly two "::": the level prefix and the message separator. A third
        # would truncate the annotation at the wrong place.
        assert line.count("::") == 2
        assert "%3A%3A" in line

    def test_commas_in_a_title_are_escaped(self):
        from api_compat.findings import format_github

        line = format_github([_finding(title="a, b changed")])
        assert "a%2C b changed" in line

    def test_newlines_and_percent_in_the_body_are_escaped(self):
        from api_compat.findings import format_github

        line = format_github([_finding(detail="one\ntwo 50% done")])
        assert "%0A" in line
        assert "50%25 done" in line
        assert "\n" not in line

    def test_severity_maps_to_a_level_github_knows(self):
        from api_compat.findings import Severity, format_github

        for sev, level in (
            (Severity.ERROR, "::error"),
            (Severity.WARNING, "::warning"),
            (Severity.NOTE, "::notice"),
        ):
            assert format_github([_finding(severity=sev)]).startswith(level)


class TestLimitToFiles:
    """Doxygen expands macro-generated headers inconsistently between runs.

    A header built from Fake Function Framework macros can expand in one
    snapshot and not the next, and every symbol it generates then looks
    removed. Those phantoms are always in files the change never touched.
    """

    def test_untouched_files_are_dropped(self):
        from api_compat.signature import limit_to_files

        kept = _finding(file="include/zephyr/kernel.h")
        phantom = _finding(file="include/zephyr/drivers/led/led_fake.h")
        out = limit_to_files([kept, phantom], {"include/zephyr/kernel.h"})
        assert [f.file for f in out] == ["include/zephyr/kernel.h"]

    def test_a_finding_with_no_file_is_dropped(self):
        from api_compat.signature import limit_to_files

        assert limit_to_files([_finding(file=None)], {"include/zephyr/kernel.h"}) == []

    def test_empty_allowed_set_drops_everything(self):
        from api_compat.signature import limit_to_files

        assert limit_to_files([_finding(file="include/zephyr/kernel.h")], set()) == []


class TestHtmlReport:
    def test_every_finding_is_reachable_by_the_filters(self):
        """A facet value with no checkbox is hidden by the page permanently.

        Findings that resolve to no group carry no lifecycle, so the filter
        list must be built from the values actually present.
        """
        from api_compat.findings import Severity as S

        page = format_html(
            [
                _finding(severity=S.WARNING, group=None, lifecycle=None),
                _finding(group="g", lifecycle="stable"),
                _finding(severity=S.NOTE, group="h", lifecycle="experimental"),
            ]
        )
        offered = {
            (m.group(1), m.group(2))
            for m in re.finditer(r'data-facet="(\w+)" value="([^"]*)"', page)
        }
        for match in re.finditer(
            r'data-sev="([^"]*)" data-life="([^"]*)" data-check="([^"]*)"', page
        ):
            for facet, value in zip(("sev", "life", "check"), match.groups(), strict=True):
                assert (facet, value) in offered, f"{facet}={value} has no filter"

    def test_markup_is_balanced(self):
        page = format_html([_finding(group="g", lifecycle="stable")])

        class Checker(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack, self.bad = [], []

            def handle_starttag(self, tag, attrs):
                if tag not in ("meta", "br", "input", "img", "hr"):
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if self.stack and self.stack[-1] == tag:
                    self.stack.pop()
                else:
                    self.bad.append(tag)

        checker = Checker()
        checker.feed(page)
        assert not checker.bad and not checker.stack

    def test_hostile_content_is_inert(self):
        page = format_html(
            [
                _finding(
                    title="<img src=x onerror=alert(1)>",
                    detail='a < b && c > d "quoted"',
                    symbol="op<T>",
                    group="g&g",
                    lifecycle="stable",
                )
            ]
        )
        assert "<img" not in page
        assert "&lt;img src=x onerror=alert(1)&gt;" in page
        assert "&amp;&amp;" in page

    def test_page_is_self_contained(self):
        page = format_html([_finding(group="g", lifecycle="stable")])
        # No CDN, font, or script host: the file must open straight from disk.
        assert "http://" not in page and "https://" not in page
        assert "<style>" in page and "<script>" in page

    def test_silent_changes_are_counted_and_labelled(self):
        page = format_html(
            [
                _finding(
                    title="silently changes behavior: x changed", group="g", lifecycle="stable"
                ),
                _finding(title="y was removed", group="g", lifecycle="stable"),
            ]
        )
        assert "Silent behavior change" in page
        assert '<div class="n">1</div><div class="k">Silent changes</div>' in page

    def test_ids_referenced_by_the_script_exist(self):
        page = format_html([_finding(group="g", lifecycle="stable")])
        for element_id in ("q", "shown", "empty", "expand", "reset"):
            assert f'id="{element_id}"' in page

    def test_metadata_is_rendered(self):
        page = format_html(
            [_finding(group="g", lifecycle="stable")],
            ReportMeta(base="v4.4.0", head="HEAD", unversioned_is="stable"),
        )
        assert "v4.4.0" in page and "stable" in page

    def test_empty_report_still_renders(self):
        page = format_html([])
        assert "<title>" in page
        assert '<div class="n">0</div><div class="k">Findings</div>' in page


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

    def test_removal_advice_matches_the_lifecycle(self):
        """The advice must not contradict the severity of the same finding.

        Only stable APIs owe a deprecation period; saying so about an
        experimental one is wrong.
        """
        for version, expected in (
            ("0.1.0", "no\ndeprecation period is required"),
            ("0.8.0", "without a deprecation"),
            ("1.0.0", "at least\ntwo releases"),
        ):
            found = compare(_snapshot(version, _fn("gone")), _snapshot(version))
            assert expected in found[0].detail, version

        experimental = compare(_snapshot("0.1.0", _fn("gone")), _snapshot("0.1.0"))
        assert "requires a deprecation period" not in experimental[0].detail

    def test_unversioned_removal_names_the_assumption(self):
        found = compare(_snapshot(None, _fn("gone")), _snapshot(None))
        assert "declares no @version" in found[0].detail
        # Relaxing the policy must relax the advice with it.
        relaxed = compare(
            _snapshot(None, _fn("gone")),
            _snapshot(None),
            unversioned_is=Lifecycle.EXPERIMENTAL,
        )
        assert "no\ndeprecation period is required" in relaxed[0].detail
        assert relaxed[0].lifecycle == "unversioned"

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

    def test_symbol_in_a_nested_group_inherits_the_parent_severity(self):
        """The clock_apis case: a subgroup of a stable API is stable.

        Without inheritance this graded as unversioned and leaned on the
        --unversioned-is policy instead of the tree's own statement.
        """
        base = ApiSnapshot(
            groups={
                "parent": GroupStatus(name="parent", version_raw="1.0.0"),
                "child": GroupStatus(name="child", parent="parent"),
            }
        )
        base.symbols = {"function:f": _fn("f")}
        base.symbols["function:f"] = Symbol(
            key="function:f", kind="function", name="f", group="child", ret="int"
        )
        head = ApiSnapshot(groups=dict(base.groups))

        found = compare(base, head)
        assert len(found) == 1
        assert found[0].severity is Severity.ERROR
        # The facet value stays a plain state name for the HTML filter.
        assert found[0].lifecycle == "stable"
        assert "covered by 'parent'" in found[0].detail

    def test_inherited_experimental_downgrades_severity(self):
        base = ApiSnapshot(
            groups={
                "parent": GroupStatus(name="parent", version_raw="0.1.0"),
                "child": GroupStatus(name="child", parent="parent"),
            }
        )
        base.symbols = {
            "function:f": Symbol(
                key="function:f", kind="function", name="f", group="child", ret="int"
            )
        }
        found = compare(base, ApiSnapshot(groups=dict(base.groups)))
        assert found[0].severity is Severity.NOTE

    def test_declaration_paths_are_repo_relative(self, tmp_path):
        """Doxygen strips include/ from the locations it records.

        A GitHub annotation only anchors to a line when the path it is given
        exists in the repository, so the prefix has to be put back.
        """
        from api_compat.signature import _repo_relative

        (tmp_path / "include" / "zephyr" / "drivers").mkdir(parents=True)
        (tmp_path / "include" / "zephyr" / "drivers" / "gpio.h").touch()
        (tmp_path / "kernel" / "include").mkdir(parents=True)
        (tmp_path / "kernel" / "include" / "ksched.h").touch()

        assert _repo_relative("zephyr/drivers/gpio.h", tmp_path) == "include/zephyr/drivers/gpio.h"
        assert _repo_relative("ksched.h", tmp_path) == "kernel/include/ksched.h"

    def test_path_already_repo_relative_is_untouched(self, tmp_path):
        from api_compat.signature import _repo_relative

        (tmp_path / "include").mkdir()
        (tmp_path / "include" / "top.h").touch()
        assert _repo_relative("include/top.h", tmp_path) == "include/top.h"

    def test_removed_header_still_gets_a_usable_path(self, tmp_path):
        """A header deleted by the change exists in no checkout to probe."""
        from api_compat.signature import _repo_relative

        assert _repo_relative("zephyr/drivers/gone.h", tmp_path) == "include/zephyr/drivers/gone.h"

    def test_unknown_path_is_left_alone(self, tmp_path):
        from api_compat.signature import _repo_relative

        assert _repo_relative("modules/foo/bar.h", tmp_path) == "modules/foo/bar.h"

    def test_changed_symbol_is_located_in_the_head_revision(self):
        """A review annotation should land on the line that changed."""
        base = ApiSnapshot(groups={"g": GroupStatus(name="g", version_raw="1.0.0")})
        base.symbols = {
            "function:f": Symbol(
                key="function:f",
                kind="function",
                name="f",
                group="g",
                ret="int",
                params=("int",),
                file="include/zephyr/x.h",
                line=10,
            )
        }
        head = ApiSnapshot(groups=dict(base.groups))
        head.symbols = {
            "function:f": Symbol(
                key="function:f",
                kind="function",
                name="f",
                group="g",
                ret="int",
                params=("long",),
                file="include/zephyr/x.h",
                line=42,
            )
        }
        found = compare(base, head)
        assert (found[0].file, found[0].line) == ("include/zephyr/x.h", 42)

    def test_removed_symbol_keeps_its_base_location(self):
        # The symbol is gone from head, so head has no line to point at.
        base = ApiSnapshot(groups={"g": GroupStatus(name="g", version_raw="1.0.0")})
        base.symbols = {
            "function:f": Symbol(
                key="function:f",
                kind="function",
                name="f",
                group="g",
                ret="int",
                file="include/zephyr/x.h",
                line=10,
            )
        }
        found = compare(base, ApiSnapshot(groups=dict(base.groups)))
        assert (found[0].file, found[0].line) == ("include/zephyr/x.h", 10)

    def test_added_symbols_are_not_reported(self):
        found = compare(_snapshot("1.0.0"), _snapshot("1.0.0", _fn("brand_new")))
        assert found == []

    def test_identical_snapshots_are_clean(self):
        base = _snapshot("1.0.0", _fn("f", params=("int",)))
        head = _snapshot("1.0.0", _fn("f", params=("int",)))
        assert compare(base, head) == []
