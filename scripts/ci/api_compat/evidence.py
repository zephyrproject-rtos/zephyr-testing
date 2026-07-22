# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Gather the evidence the lifecycle criteria ask for.

Three measurements, each with a documented failure mode:

**Releases** — from history.py, following renames.

**Implementations** — counted from the ``DEVICE_API(<class>, …) = {`` definitions
of the API class the header declares. The class is read out of the header's
``struct <class>_driver_api`` rather than inferred from the filename, because
the two disagree often enough to matter: ``watchdog.h`` declares class ``wdt``,
and ``uart.h`` keeps its vtable in a nested ``_internal.h``. The implementation
directory is likewise never derived from the path, since roughly twenty headers
do not follow ``drivers/<basename>/`` at all (``uart.h`` is implemented in
``drivers/serial/``).

  Critically, about eighteen driver classes have **no vtable at all** — pinctrl,
  hwinfo, cache, timer and input among them — because they dispatch directly or
  through syscalls. For those the implementation count is not zero, it is
  *unknown*, and treating it as zero would mark long-stable APIs experimental.
  Such APIs fall back to the hardware-agnostic rule.

**Users** — files outside include/ that include the header. A proxy for the
document's "in use", not a rule from it.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from pathlib import Path

from .gitutil import GitError, git

#: Binding directories whose name does not match the header basename.
_BINDING_ALIASES = {
    "uart": "serial",
    "flash": "flash_controller",
    "entropy": "rng",
    "clock_control": "clock",
}

#: Compatible prefixes that name no silicon vendor.
_PSEUDO_VENDORS = {"zephyr", "linaro"}

#: Implementations that exist only for testing do not demonstrate that an API
#: works on real hardware.
_NON_HARDWARE_RE = re.compile(r"_(test|emul)\.c$")

_VTABLE_RE = re.compile(r"struct\s+([a-z0-9_]+)_driver_api\s*\{")
#: A definition, not a forward declaration: requires the trailing "= {".
_DEVICE_API_RE = r"DEVICE_API\(%s,[^)]*\)\s*="
_COMPATIBLE_RE = re.compile(r'^compatible:\s*"([A-Za-z0-9_.-]+),', re.MULTILINE)


@dataclass
class Implementations:
    """What the tree offers as implementations of one API class."""

    #: None when the API uses no vtable, which is not the same as zero.
    count: int | None
    files: list[str]
    vendors: int | None = None

    @property
    def measurable(self) -> bool:
        return self.count is not None


def api_class_of(repo: Path, header: str) -> str | None:
    """Return the driver API class a header declares, if any.

    Reads ``struct <class>_driver_api`` out of the header, falling back to the
    sibling ``include/zephyr/drivers/<base>/`` directory, where APIs such as
    uart keep their vtable in an ``_internal.h``.
    """
    full = repo / header
    if not full.exists():
        return None

    match = _VTABLE_RE.search(full.read_text(encoding="utf-8", errors="replace"))
    if match:
        return match.group(1)

    sibling = full.parent / full.stem
    if sibling.is_dir():
        for candidate in sorted(sibling.glob("*.h")):
            match = _VTABLE_RE.search(candidate.read_text(encoding="utf-8", errors="replace"))
            if match:
                return match.group(1)
    return None


def implementations_of(repo: Path, api_class: str | None) -> Implementations:
    """Count in-tree implementations of an API class."""
    if not api_class:
        return Implementations(count=None, files=[])

    try:
        out = git(
            "grep",
            "-lE",
            _DEVICE_API_RE % re.escape(api_class),
            "--",
            "drivers/",
            "soc/",
            "boards/",
            cwd=repo,
        )
    except GitError:
        # git grep exits non-zero when nothing matches.
        return Implementations(count=None, files=[])

    files = [f for f in out.splitlines() if f and not _NON_HARDWARE_RE.search(f)]
    if not files:
        # The class exists but nothing defines a vtable for it: direct dispatch.
        return Implementations(count=None, files=[])
    return Implementations(count=len(files), files=sorted(files))


def vendors_of(repo: Path, header: str) -> int | None:
    """Distinct silicon vendors with a devicetree binding for this API.

    The vendor prefix of a ``compatible:`` is a validated namespace, so it
    counts hardware rather than files. Returns None when no binding directory
    corresponds to the header.
    """
    stem = Path(header).stem
    names = {_BINDING_ALIASES.get(stem, stem)}
    names |= {n.replace("_", "-") for n in set(names)}

    for name in names:
        directory = repo / "dts" / "bindings" / name
        if not directory.is_dir():
            continue
        vendors: set[str] = set()
        for binding in directory.rglob("*.yaml"):
            text = binding.read_text(encoding="utf-8", errors="replace")
            for match in _COMPATIBLE_RE.finditer(text):
                prefix = match.group(1).lower()
                if prefix not in _PSEUDO_VENDORS:
                    vendors.add(prefix)
        return len(vendors)
    return None


def is_extension_header(header: str) -> bool:
    """Whether a header extends one API rather than declaring its own.

    Two kinds are not APIs in their own right and should not be proposed a
    lifecycle state alongside real ones:

    * headers nested inside a driver class directory, such as
      include/zephyr/drivers/clock_control/stm32_clock_control.h or
      include/zephyr/drivers/sensor/<part>.h, which are per-vendor or per-chip
      extensions of the class above them,
    * emulator headers, which exist for testing.

    Without this, a vendor clock-control header picks up the SoC files that
    include it as "users" and is proposed stable, which it is not: it is one
    vendor's extension to an API that already has its own state.
    """
    path = Path(header)
    parts = path.parts
    nested_under_class = len(parts) > 4 and parts[2] == "drivers"
    emulator = "emul" in path.stem.split("_")
    return nested_under_class or emulator


def implementation_dirs(files: list[str]) -> set[str]:
    """The directories an API's own implementations live in.

    Used to keep an API's implementations from being counted as its users. A
    niche API is mostly included by its own drivers, so without this a driver
    with two implementations and almost no consumers looks widely adopted.
    """
    return {str(Path(f).parent) for f in files}


def users_of(
    repo: Path, header: str, exclude_dirs: set[str] | None = None
) -> tuple[int, dict[str, int]]:
    """Files outside include/ that include this header.

    Implementations of the API itself are excluded: a driver including the
    header it implements is not evidence that anyone uses the API.
    """
    # The include path as written in source, e.g. <zephyr/drivers/gpio.h>.
    relative = header[len("include/") :] if header.startswith("include/") else header
    excludes = [f":!{directory}/" for directory in sorted(exclude_dirs or ())]
    try:
        out = git(
            "grep", "-l", f"include <{relative}>", "--", ":!include/", ":!doc/", *excludes, cwd=repo
        )
    except GitError:
        return (0, {})

    files = [f for f in out.splitlines() if f]
    breakdown = collections.Counter(f.split("/", 1)[0] for f in files)
    return (len(files), dict(breakdown.most_common()))


def gather(repo: Path, header: str, releases, added: int | None) -> list:
    """Collect the evidence for every group declared in one header."""
    from . import apidoc
    from .history import age_of
    from .propose import Evidence, Kind

    info = apidoc.scan_file(repo / header, header)
    if not info.groups:
        return []

    api_class = api_class_of(repo, header)
    impls = implementations_of(repo, api_class)
    vendors = vendors_of(repo, header)
    users, breakdown = users_of(repo, header, implementation_dirs(impls.files))
    age = age_of(releases, added)

    # An API is judged by the peripheral rule only when its implementations can
    # actually be counted. Classes that dispatch without a vtable would
    # otherwise look like they had none at all.
    if impls.measurable:
        kind = Kind.PERIPHERAL
        # Vendors count hardware rather than files; the lower of the two is the
        # honest answer to "implementations on different hardware platforms".
        count = min(impls.count, vendors) if vendors else impls.count
    else:
        kind = Kind.AGNOSTIC
        count = 0

    return [
        Evidence(
            group=group,
            header=header,
            kind=kind,
            releases=age.releases,
            first_release=age.first.short if age.first else None,
            implementations=count,
            implementation_examples=impls.files[:3],
            users=users,
            user_breakdown=breakdown,
        )
        for group in info.groups.values()
    ]


def gather_all(repo: Path, headers: list[str], releases, workers: int = 8) -> list:
    """Collect evidence for many headers, in parallel.

    Each header costs two git greps and a rename-following log, so the work is
    spread over a thread pool; the calls are independent subprocesses.
    """
    import concurrent.futures

    from .history import added_timestamps

    stamps = added_timestamps(repo, headers, workers=workers)

    collected: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(gather, repo, header, releases, stamps.get(header)) for header in headers
        ]
        for future in concurrent.futures.as_completed(futures):
            collected.extend(future.result())
    return collected
