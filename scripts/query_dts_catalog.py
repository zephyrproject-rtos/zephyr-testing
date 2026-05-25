#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Query the DTS hardware catalog produced by gen_dts_catalog.py.

Usage
-----
::

    # Summary statistics
    query_dts_catalog.py catalog.json stats

    # List all boards (optional name filter)
    query_dts_catalog.py catalog.json boards
    query_dts_catalog.py catalog.json boards --filter sam

    # List / search compatible strings
    query_dts_catalog.py catalog.json compat
    query_dts_catalog.py catalog.json compat --filter spi
    query_dts_catalog.py catalog.json compat --type serial

    # Show all hardware for a board
    query_dts_catalog.py catalog.json hardware nrf52840dk
    query_dts_catalog.py catalog.json hardware nrf52840dk --okay-only
    query_dts_catalog.py catalog.json hardware nrf52840dk --type serial

    # Which boards use a given compatible string?
    query_dts_catalog.py catalog.json who-uses nordic,nrf-uart
    query_dts_catalog.py catalog.json who-uses "nordic,nrf" --partial

    # Show all targets for a board
    query_dts_catalog.py catalog.json targets nrf52840dk

All subcommands accept ``--json`` to emit machine-readable JSON instead of the
default human-readable output.
"""

import argparse
import fnmatch
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Database loader
# ---------------------------------------------------------------------------

def load_db(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        _die(f"catalog file not found: {path}")
    except json.JSONDecodeError as exc:
        _die(f"catalog file is not valid JSON: {exc}")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def emit(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        _print_human(data)


def _print_human(data) -> None:
    """Recursively pretty-print the result object."""
    if isinstance(data, str):
        print(data)
        return
    if isinstance(data, list):
        for item in data:
            _print_human(item)
        return
    if isinstance(data, dict):
        _print_dict(data, indent=0)
        return


def _print_dict(d: dict, indent: int) -> None:
    pad = "  " * indent
    for key, val in d.items():
        if isinstance(val, (dict,)):
            print(f"{pad}{key}:")
            _print_dict(val, indent + 1)
        elif isinstance(val, list):
            if all(isinstance(v, str) for v in val):
                print(f"{pad}{key}: {', '.join(val)}")
            else:
                print(f"{pad}{key}:")
                for item in val:
                    if isinstance(item, dict):
                        _print_dict(item, indent + 1)
                    else:
                        print(f"{pad}  - {item}")
        else:
            print(f"{pad}{key}: {val}")


def _table(rows: list[tuple], headers: list[str]) -> None:
    """Print a simple fixed-width table."""
    if not rows:
        print("(no results)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    sep = "  ".join("-" * w for w in widths)
    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


# ---------------------------------------------------------------------------
# Helpers shared across subcommands
# ---------------------------------------------------------------------------

def _resolve_board(db: dict, name: str) -> str:
    """Return the exact board name, case-insensitively, or die."""
    if name in db["boards"]:
        return name
    lower = name.lower()
    matches = [b for b in db["boards"] if b.lower() == lower]
    if len(matches) == 1:
        return matches[0]
    if matches:
        _die(f"ambiguous board name '{name}': {matches}")
    _die(f"board '{name}' not found in catalog")


def _pick_target(board_entry: dict, target: str | None) -> tuple[str, dict]:
    """Return (target_name, target_dict), defaulting to the first target."""
    targets = board_entry["targets"]
    if target:
        if target not in targets:
            _die(
                f"target '{target}' not found; available: {', '.join(targets.keys())}"
            )
        return target, targets[target]
    name = next(iter(targets))
    return name, targets[name]


# ---------------------------------------------------------------------------
# Subcommand: stats
# ---------------------------------------------------------------------------

def cmd_stats(db: dict, args: argparse.Namespace) -> None:
    boards = db["boards"]
    compatibles = db["compatibles"]

    total_targets = sum(len(b["targets"]) for b in boards.values())

    type_counts: dict[str, int] = {}
    for entry in compatibles.values():
        bt = entry.get("binding_type", "misc")
        type_counts[bt] = type_counts.get(bt, 0) + 1

    result = {
        "generated_at": db.get("generated_at", "unknown"),
        "zephyr_base": db.get("zephyr_base", "unknown"),
        "boards": len(boards),
        "targets": total_targets,
        "unique_compatibles": len(compatibles),
        "compatibles_by_type": dict(
            sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
        ),
    }

    if args.json:
        emit(result, as_json=True)
    else:
        print(f"Generated at : {result['generated_at']}")
        print(f"Zephyr base  : {result['zephyr_base']}")
        print(f"Boards       : {result['boards']}")
        print(f"Targets      : {result['targets']}")
        print(f"Compatibles  : {result['unique_compatibles']}")
        print()
        print("Compatibles by binding type:")
        rows = [(bt, cnt) for bt, cnt in result["compatibles_by_type"].items()]
        _table(rows, ["Type", "Count"])


# ---------------------------------------------------------------------------
# Subcommand: boards
# ---------------------------------------------------------------------------

def cmd_boards(db: dict, args: argparse.Namespace) -> None:
    pattern = args.filter or ""
    results = []
    for name in sorted(db["boards"]):
        if pattern and not fnmatch.fnmatch(name.lower(), f"*{pattern.lower()}*"):
            continue
        entry = db["boards"][name]
        targets = list(entry["targets"].keys())
        results.append({"board": name, "targets": targets})

    if args.json:
        emit(results, as_json=True)
    else:
        rows = [(r["board"], ", ".join(r["targets"])) for r in results]
        _table(rows, ["Board", "Targets"])
        print(f"\n{len(results)} board(s) shown.")


# ---------------------------------------------------------------------------
# Subcommand: targets
# ---------------------------------------------------------------------------

def cmd_targets(db: dict, args: argparse.Namespace) -> None:
    board = _resolve_board(db, args.board)
    targets = list(db["boards"][board]["targets"].keys())

    if args.json:
        emit({"board": board, "targets": targets}, as_json=True)
    else:
        print(f"Board: {board}")
        for t in targets:
            print(f"  {t}")


# ---------------------------------------------------------------------------
# Subcommand: compat
# ---------------------------------------------------------------------------

def cmd_compat(db: dict, args: argparse.Namespace) -> None:
    pattern = args.filter or ""
    binding_type = args.type or ""

    results = []
    for name in sorted(db["compatibles"]):
        entry = db["compatibles"][name]
        if pattern and not fnmatch.fnmatch(name.lower(), f"*{pattern.lower()}*"):
            continue
        if binding_type and entry.get("binding_type", "") != binding_type:
            continue
        results.append(
            {
                "compatible": name,
                "binding_type": entry.get("binding_type", ""),
                "description": entry.get("description", ""),
                "boards": entry.get("boards", []),
                "board_count": len(entry.get("boards", [])),
            }
        )

    if args.json:
        emit(results, as_json=True)
    else:
        rows = [
            (r["compatible"], r["binding_type"], r["board_count"], r["description"][:60])
            for r in results
        ]
        _table(rows, ["Compatible", "Type", "#Boards", "Description"])
        print(f"\n{len(results)} compatible(s) shown.")


# ---------------------------------------------------------------------------
# Subcommand: who-uses
# ---------------------------------------------------------------------------

def cmd_who_uses(db: dict, args: argparse.Namespace) -> None:
    pattern = args.compatible

    if args.partial:
        # Return all compatibles whose name contains the pattern.
        matches = {
            c: entry
            for c, entry in db["compatibles"].items()
            if pattern.lower() in c.lower()
        }
    else:
        if pattern not in db["compatibles"]:
            _die(
                f"compatible '{pattern}' not in catalog.  "
                f"Use --partial for substring search."
            )
        matches = {pattern: db["compatibles"][pattern]}

    if args.json:
        emit(
            [
                {
                    "compatible": c,
                    "binding_type": e.get("binding_type", ""),
                    "description": e.get("description", ""),
                    "boards": e.get("boards", []),
                }
                for c, e in sorted(matches.items())
            ],
            as_json=True,
        )
    else:
        for compat, entry in sorted(matches.items()):
            boards = entry.get("boards", [])
            print(f"{compat}  [{entry.get('binding_type', '')}]")
            print(f"  {entry.get('description', '')}")
            if boards:
                for b in sorted(boards):
                    print(f"    - {b}")
            else:
                print("    (no boards)")
            print()


# ---------------------------------------------------------------------------
# Subcommand: hardware — pretty-print helpers
# ---------------------------------------------------------------------------

_PRETTY_WIDTH = 72

_TYPE_LABEL: dict[str, str] = {
    "adc":                  "ADC",
    "audio":                "AUDIO",
    "bluetooth":            "BT",
    "can":                  "CAN",
    "clock":                "CLOCK",
    "dac":                  "DAC",
    "display":              "DISPLAY",
    "dma":                  "DMA",
    "ethernet":             "ETH",
    "flash":                "FLASH",
    "gpio":                 "GPIO",
    "i2c":                  "I2C",
    "input":                "INPUT",
    "interrupt-controller": "IRQ",
    "led":                  "LED",
    "pinctrl":              "PINCTRL",
    "pwm":                  "PWM",
    "rtc":                  "RTC",
    "sensor":               "SENSOR",
    "serial":               "SERIAL",
    "spi":                  "SPI",
    "timer":                "TIMER",
    "usb":                  "USB",
    "watchdog":             "WATCHDOG",
    "wifi":                 "WIFI",
}


def _hw_box_line(text: str, width: int) -> str:
    """Return a box-border line padded to *width* total characters."""
    return "│" + text + " " * (width - 2 - len(text)) + "│"


def _print_hardware_pretty(
    board: str,
    target_name: str,
    result: dict,
    okay_only: bool,
    width: int = _PRETTY_WIDTH,
) -> None:
    """Render a hardware catalogue entry as a structured ASCII tree."""
    W = width

    # ── header box ──────────────────────────────────────────────────────────
    print("┌" + "─" * (W - 2) + "┐")
    print(_hw_box_line(f"  Board  : {board}", W))
    print(_hw_box_line(f"  Target : {target_name}", W))
    if okay_only:
        print(_hw_box_line("  Filter : okay nodes only", W))
    print("└" + "─" * (W - 2) + "┘")
    print()

    if not result:
        print("  (no hardware entries matched)")
        return

    total_ok = 0
    total_dis = 0

    for btype in sorted(result.keys()):
        compats = result[btype]
        label = _TYPE_LABEL.get(btype, btype.upper())
        n = len(compats)
        count_str = f"{n} compatible{'s' if n != 1 else ''}"

        # Section header: "  ┬  SERIAL  ──────────────  2 compatibles"
        header_prefix = f"  ┬  {label}  "
        dash_count = max(4, W - len(header_prefix) - len(count_str) - 2)
        print(header_prefix + "─" * dash_count + "  " + count_str)

        compat_items = sorted(compats.items())
        for idx, (compat, info) in enumerate(compat_items):
            is_last = idx == len(compat_items) - 1
            is_okay = info.get("okay", False)

            if is_okay:
                total_ok += 1
                dot = "●"
                tag = "[okay]    "
            else:
                total_dis += 1
                dot = "○"
                tag = "[disabled]"

            connector = "└─" if is_last else "├─"
            cont = "   " if is_last else "│  "

            # Compat line with right-aligned status tag
            prefix = f"  {connector} {dot} "
            tag_col = W - len(tag)
            line = prefix + compat
            padding = max(1, tag_col - len(line))
            print(line + " " * padding + tag)

            # Locations
            locs = info.get("locations", [])
            if locs:
                max_locs_w = W - len(cont) - 14
                locs_joined = "  ".join(locs)
                if len(locs_joined) > max_locs_w:
                    truncated: list[str] = []
                    used = 0
                    for loc in locs:
                        if used + len(loc) + 2 > max_locs_w - 12:
                            truncated.append(f"(+{len(locs) - len(truncated)} more)")
                            break
                        truncated.append(loc)
                        used += len(loc) + 2
                    locs_joined = "  ".join(truncated)
                print(f"  {cont}  Locations: {locs_joined}")

            # Description
            desc = (info.get("description") or "").strip()
            if desc:
                max_d = W - len(cont) - 6
                if len(desc) > max_d:
                    desc = desc[: max_d - 3] + "..."
                print(f"  {cont}  {desc}")

            if not is_last:
                print("  │")

        print()

    # ── summary footer ───────────────────────────────────────────────────────
    total = total_ok + total_dis
    print("  " + "─" * (W - 4))
    parts = [
        f"{len(result)} type{'s' if len(result) != 1 else ''}",
        f"{total} compatible{'s' if total != 1 else ''}",
        f"{total_ok} okay",
        f"{total_dis} disabled",
    ]
    print("  " + "  |  ".join(parts))


# ---------------------------------------------------------------------------
# Subcommand: hardware
# ---------------------------------------------------------------------------

def cmd_hardware(db: dict, args: argparse.Namespace) -> None:
    board = _resolve_board(db, args.board)
    board_entry = db["boards"][board]

    target_name, target = _pick_target(board_entry, args.target)

    hardware = target["hardware"]
    okay_only = args.okay_only
    binding_type_filter = args.type or ""

    result: dict = {}
    for btype, compats in hardware.items():
        if binding_type_filter and btype != binding_type_filter:
            continue
        filtered = {}
        for compat, info in compats.items():
            if okay_only and not info.get("okay", False):
                continue
            filtered[compat] = info
        if filtered:
            result[btype] = filtered

    if args.json:
        emit(
            {"board": board, "target": target_name, "hardware": result},
            as_json=True,
        )
    elif getattr(args, "pretty", False):
        _print_hardware_pretty(board, target_name, result, okay_only)
    else:
        status_tag = " [okay only]" if okay_only else ""
        type_tag = f" [type={binding_type_filter}]" if binding_type_filter else ""
        print(f"Board  : {board}")
        print(f"Target : {target_name}{status_tag}{type_tag}")
        print()
        for btype in sorted(result):
            print(f"  [{btype}]")
            rows = []
            for compat, info in sorted(result[btype].items()):
                ok = "yes" if info.get("okay") else "no"
                locs = ", ".join(info.get("locations", []))
                desc = (info.get("description") or "")[:55]
                rows.append((compat, ok, locs, desc))
            _table(rows, ["Compatible", "Okay", "Location", "Description"])
            print()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("catalog", metavar="CATALOG", help="Path to the JSON catalog file.")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # -- stats ----------------------------------------------------------------
    p = sub.add_parser("stats", help="Show catalog summary statistics.")
    p.add_argument("--json", action="store_true", help="Output as JSON.")

    # -- boards ---------------------------------------------------------------
    p = sub.add_parser("boards", help="List boards in the catalog.")
    p.add_argument(
        "--filter", "-f", metavar="PATTERN",
        help="Show only boards whose name contains PATTERN.",
    )
    p.add_argument("--json", action="store_true", help="Output as JSON.")

    # -- targets --------------------------------------------------------------
    p = sub.add_parser("targets", help="List build targets for a board.")
    p.add_argument("board", metavar="BOARD")
    p.add_argument("--json", action="store_true", help="Output as JSON.")

    # -- compat ---------------------------------------------------------------
    p = sub.add_parser("compat", help="List or search compatible strings.")
    p.add_argument(
        "--filter", "-f", metavar="PATTERN",
        help="Show only compatibles whose name contains PATTERN.",
    )
    p.add_argument(
        "--type", "-t", metavar="TYPE",
        help="Filter by binding type (e.g. serial, gpio, sensor).",
    )
    p.add_argument("--json", action="store_true", help="Output as JSON.")

    # -- who-uses -------------------------------------------------------------
    p = sub.add_parser("who-uses", help="List boards that use a compatible string.")
    p.add_argument("compatible", metavar="COMPATIBLE")
    p.add_argument(
        "--partial", "-p", action="store_true",
        help="Treat COMPATIBLE as a substring pattern.",
    )
    p.add_argument("--json", action="store_true", help="Output as JSON.")

    # -- hardware -------------------------------------------------------------
    p = sub.add_parser("hardware", help="Show hardware for a board.")
    p.add_argument("board", metavar="BOARD")
    p.add_argument(
        "--target", metavar="TARGET",
        help="Board target (default: first available target).",
    )
    p.add_argument(
        "--type", "-t", metavar="TYPE",
        help="Filter by binding type (e.g. serial, gpio, sensor).",
    )
    p.add_argument(
        "--okay-only", "-O", action="store_true",
        help="Show only nodes with status = okay.",
    )
    p.add_argument("--json", action="store_true", help="Output as JSON.")
    p.add_argument(
        "--pretty", "-P", action="store_true",
        help="Render output as a structured ASCII tree (ignored when --json is set).",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_COMMANDS = {
    "stats": cmd_stats,
    "boards": cmd_boards,
    "targets": cmd_targets,
    "compat": cmd_compat,
    "who-uses": cmd_who_uses,
    "hardware": cmd_hardware,
}


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    db = load_db(args.catalog)
    _COMMANDS[args.command](db, args)


if __name__ == "__main__":
    main()
