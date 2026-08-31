# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Propose a lifecycle state for API groups that do not declare one.

About 78% of the groups in the tree carry no @version, so the signature check
has to guess at their guarantees. This module narrows that backlog by gathering
the evidence doc/develop/api/api_lifecycle.rst actually asks for and proposing a
state per group.

The promotion criteria the document states are:

* experimental -> unstable: a peripheral API needs at least two implementations
  on different hardware platforms; a hardware-agnostic API needs multiple
  applications using it.
* unstable -> stable: 100% test coverage, complete in-code documentation, and
  the API must have been in use and available in at least two development
  releases.

Two of those are measurable from the tree (releases, implementations) and one
is a reasonable proxy (in-tree users). **Test coverage and documentation
completeness are not measured here**, so a "stable" proposal is a candidate for
review, never a verdict: the remaining criteria are for a human and the
Architecture WG to judge.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from .apidoc import Group, Lifecycle

#: A hardware-agnostic API needs to have shipped in this many releases before
#: it is worth calling anything other than experimental.
MIN_RELEASES_UNSTABLE = 2

#: "available in at least two development releases" is the floor for stable;
#: requiring strictly more than two keeps the proposal conservative.
MIN_RELEASES_STABLE = 3

#: "at least two implementations on different hardware platforms".
MIN_IMPLEMENTATIONS = 2

#: In-tree users standing in for "in use". Deliberately blunt: it is a proxy
#: for the document's "multiple applications using it", not a rule from it.
SUBSTANTIAL_USERS = 10


class Kind(enum.Enum):
    """Which promotion rule applies to an API."""

    #: Lives under include/zephyr/drivers/: judged on implementations.
    PERIPHERAL = "peripheral"
    #: Everything else: judged on releases and users.
    AGNOSTIC = "hardware-agnostic"


@dataclass
class Evidence:
    """What the tree says about one API group."""

    group: Group
    header: str
    kind: Kind
    #: Releases the header has appeared in, and the first one.
    releases: int = 0
    first_release: str | None = None
    #: Independent implementations found, with a few examples.
    implementations: int = 0
    implementation_examples: list[str] = field(default_factory=list)
    #: In-tree files including this header, excluding its own implementations.
    users: int = 0
    user_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def declared(self) -> Lifecycle:
        return self.group.lifecycle


@dataclass
class Proposal:
    """A proposed state, with the reasoning that produced it."""

    state: Lifecycle
    reasons: list[str]
    #: True when the proposal needs criteria this tool cannot measure.
    needs_review: bool = False

    @property
    def disagrees_with_declaration(self) -> bool:
        return False


def classify(evidence: Evidence) -> Proposal:
    """Propose a lifecycle state from the gathered evidence."""
    releases = evidence.releases
    users = evidence.users
    impls = evidence.implementations
    reasons: list[str] = []

    if not releases:
        return Proposal(
            Lifecycle.EXPERIMENTAL,
            ["has not appeared in any tagged release yet"],
        )

    release_note = (
        f"shipped in {releases} release{'s' if releases != 1 else ''}"
        f"{f' since {evidence.first_release}' if evidence.first_release else ''}"
    )

    if evidence.kind is Kind.PERIPHERAL:
        impl_note = f"{impls} in-tree implementation{'s' if impls != 1 else ''}"

        if releases < MIN_RELEASES_UNSTABLE or impls < MIN_IMPLEMENTATIONS:
            reasons.append(release_note)
            reasons.append(impl_note)
            if impls < MIN_IMPLEMENTATIONS:
                reasons.append(
                    f"promotion out of experimental wants at least "
                    f"{MIN_IMPLEMENTATIONS} implementations on different hardware"
                )
            else:
                reasons.append(
                    f"promotion out of experimental wants at least {MIN_RELEASES_UNSTABLE} releases"
                )
            return Proposal(Lifecycle.EXPERIMENTAL, reasons)

        if releases >= MIN_RELEASES_STABLE and users >= SUBSTANTIAL_USERS:
            return Proposal(
                Lifecycle.STABLE,
                [release_note, impl_note, f"{users} in-tree users"],
                needs_review=True,
            )

        reasons = [release_note, impl_note]
        if users < SUBSTANTIAL_USERS:
            reasons.append(f"only {users} in-tree users, below the bar for stable")
        return Proposal(Lifecycle.UNSTABLE, reasons)

    # Hardware-agnostic: no implementations to count, so age and use decide.
    if releases < MIN_RELEASES_UNSTABLE:
        return Proposal(
            Lifecycle.EXPERIMENTAL,
            [release_note, f"fewer than {MIN_RELEASES_UNSTABLE} releases"],
        )

    if releases >= MIN_RELEASES_STABLE and users >= SUBSTANTIAL_USERS:
        return Proposal(
            Lifecycle.STABLE,
            [release_note, f"{users} in-tree users"],
            needs_review=True,
        )

    reasons = [release_note, f"{users} in-tree users"]
    if users < SUBSTANTIAL_USERS:
        reasons.append(f"fewer than {SUBSTANTIAL_USERS} users, below the bar for stable")
    return Proposal(Lifecycle.UNSTABLE, reasons)


#: Version string to suggest for each proposed state, seeded as
#: doc/develop/api/overview.rst describes.
SEED_VERSION = {
    Lifecycle.EXPERIMENTAL: "0.1.0",
    Lifecycle.UNSTABLE: "0.8.0",
    Lifecycle.STABLE: "1.0.0",
}


def to_finding(evidence: Evidence, proposal: Proposal):
    """Render one proposal as a Finding, so every output format works."""
    from .findings import Finding, Severity

    declared = evidence.declared
    current = (
        "no @version"
        if declared is Lifecycle.UNVERSIONED
        else f"@version {evidence.group.version_raw}"
    )
    agrees = declared is proposal.state

    lines = [f"Evidence ({evidence.kind.value}):"]
    lines += [f"  - {reason}" for reason in proposal.reasons]

    if evidence.implementation_examples:
        shown = ", ".join(evidence.implementation_examples)
        lines.append(f"  - for example: {shown}")
    if evidence.user_breakdown:
        top = ", ".join(f"{k}: {v}" for k, v in list(evidence.user_breakdown.items())[:4])
        lines.append(f"  - users by tree: {top}")

    lines.append("")
    if agrees:
        lines.append("The declared state already matches; nothing to change.")
    else:
        lines.append(
            f"Suggested tags on the @defgroup:\n"
            f"    @since {evidence.first_release or '<next release>'}\n"
            f"    @version {SEED_VERSION[proposal.state]}"
        )

    if proposal.needs_review:
        lines.append(
            "\nStable additionally requires 100% test coverage and complete\n"
            "in-code documentation, which this tool does not measure, plus\n"
            "review at the Architecture WG. Treat this as a candidate only."
        )

    verb = "confirms" if agrees else "proposes"
    return Finding(
        check="lifecycle-proposal",
        severity=Severity.NOTE,
        title=f"{verb} {proposal.state.value} for '{evidence.group.name}' (currently {current})",
        detail="\n".join(lines),
        file=evidence.header,
        line=evidence.group.line,
        group=evidence.group.name,
        lifecycle=declared.value,
    )
