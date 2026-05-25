#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Generate a JSON database of devicetree hardware for all Zephyr boards.

This script runs twister in cmake-only mode to trigger the devicetree
elaboration step for every board, then harvests the resulting EDT pickle
files and produces a structured JSON catalog that maps:

  - each board/target   → set of DT-compatible strings it uses
  - each compatible     → set of boards/targets that use it

Typical usage::

    # Build the database (may take a long time for --all):
    python scripts/gen_dts_catalog.py --output dts_catalog.json

    # Limit to a subset of vendors to iterate quickly:
    python scripts/gen_dts_catalog.py --output dts_catalog.json \\
        --vendor nordic --vendor st

    # Re-use a twister output directory from a previous run:
    python scripts/gen_dts_catalog.py --output dts_catalog.json \\
        --twister-outdir /tmp/my-twister-out --skip-twister

Database schema (top-level keys)
---------------------------------
generated_at : str
    ISO-8601 timestamp of generation.
zephyr_base : str
    Absolute path of the Zephyr tree used for generation.
boards : dict[board_name, BoardEntry]
    Per-board data.  Each ``BoardEntry`` is::

        {
          "targets": {
            "<board_target>": {
              "compatibles": ["compat-a", "compat-b", ...],
              "hardware": {
                "<binding-type>": {
                  "<compatible>": {
                    "description": str,
                    "title": str,
                    "locations": ["board" | "soc"],
                    "okay": bool,
                    "dts_sources": [{"file": str, "line": int}]
                  }
                }
              }
            }
          }
        }

compatibles : dict[compatible_str, CompatEntry]
    Reverse index.  Each ``CompatEntry`` is::

        {
          "description": str,
          "title": str,
          "binding_type": str,
          "boards": ["board_name", ...]
        }
"""

import argparse
import datetime
import json
import logging
import os
import pickle
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

import yaml

ZEPHYR_BASE = Path(__file__).parents[1]

# West workspace virtual environment (created by pip install -r requirements.txt
# or `west setup`).  Activating it ensures all Zephyr Python dependencies
# (twister, devicetree, runners, …) are available both in this process and in
# the twister sub-process.
_WEST_VENV = ZEPHYR_BASE.parent / ".venv"
_VENV_BIN = _WEST_VENV / "bin"
_VENV_PYTHON = _VENV_BIN / "python3"

# The devicetree package is not installed globally or inside the venv; it lives
# as in-tree source.  Add it unconditionally so that pickle.load() can
# deserialise EDT objects regardless of how the script is invoked.
_DEVICETREE_SRC = ZEPHYR_BASE / "scripts" / "dts" / "python-devicetree" / "src"
if str(_DEVICETREE_SRC) not in sys.path:
    sys.path.insert(0, str(_DEVICETREE_SRC))

EDT_PICKLE_PATHS = (
    "zephyr/edt.pickle",
    "hello_world/zephyr/edt.pickle",  # for board targets using sysbuild
)
RUNNERS_YAML_PATHS = (
    "zephyr/runners.yaml",
    "hello_world/zephyr/runners.yaml",  # for board targets using sysbuild
)

ZEPHYR_BINDINGS = ZEPHYR_BASE / "dts" / "bindings"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_binding_type(binding_path: Path) -> str:
    """Return the binding-type category for a binding file path.

    Mirrors the logic in ``doc/_scripts/dts_binding_types.py``
    without requiring a cross-directory import.
    """
    if binding_path.is_relative_to(ZEPHYR_BINDINGS):
        return binding_path.relative_to(ZEPHYR_BINDINGS).parts[0]
    return "misc"


# ---------------------------------------------------------------------------
# Twister execution
# ---------------------------------------------------------------------------

def run_twister_cmake_only(outdir: Path, vendor_filter: list) -> None:
    """Run twister in cmake-only mode to generate build-info and EDT files.

    Args:
        outdir: Directory where twister should write its output.
        vendor_filter: If non-empty, restrict to boards from these vendors.
    """
    python = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

    twister_cmd = [
        python,
        str(ZEPHYR_BASE / "scripts" / "twister"),
        "-T",
        "samples/hello_world/",
        "-M",
        *[arg for path in EDT_PICKLE_PATHS for arg in ("--keep-artifacts", path)],
        *[arg for path in RUNNERS_YAML_PATHS for arg in ("--keep-artifacts", path)],
        "--cmake-only",
        "-v",
        "--outdir",
        str(outdir),
    ]

    if vendor_filter:
        for vendor in vendor_filter:
            twister_cmd += ["--vendor", vendor]
    else:
        twister_cmd += ["--all"]

    path = os.environ.get("PATH", "")
    if _VENV_BIN.exists():
        path = str(_VENV_BIN) + os.pathsep + path

    minimal_env = {
        "PATH": path,
        "ZEPHYR_BASE": str(ZEPHYR_BASE),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }
    if _WEST_VENV.exists():
        minimal_env["VIRTUAL_ENV"] = str(_WEST_VENV)

    logger.info("Running twister (cmake-only) …")
    logger.debug("Command: %s", " ".join(twister_cmd))

    try:
        subprocess.run(twister_cmd, check=True, cwd=ZEPHYR_BASE, env=minimal_env)
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Twister exited with an error; the catalog may be incomplete.\n%s", exc
        )


# ---------------------------------------------------------------------------
# Build-info harvesting
# ---------------------------------------------------------------------------

def gather_board_edts(twister_out_dir: Path) -> dict:
    """Load EDT objects for every board target from a twister output directory.

    Args:
        twister_out_dir: Root of the twister output tree.

    Returns:
        Nested dict ``{board_name: {board_target: edt_object}}``.
    """
    board_edts = {}

    if not twister_out_dir.exists():
        logger.warning("Twister output directory does not exist: %s", twister_out_dir)
        return board_edts

    build_info_files = list(twister_out_dir.glob("*/**/build_info.yml"))
    logger.info("Found %d build_info.yml files", len(build_info_files))

    for build_info_file in build_info_files:
        edt_pickle_file = None
        for pickle_path in EDT_PICKLE_PATHS:
            candidate = build_info_file.parent / pickle_path
            if candidate.exists():
                edt_pickle_file = candidate
                break

        if not edt_pickle_file:
            continue

        try:
            with open(build_info_file) as fh:
                build_info = yaml.safe_load(fh)
                board_info = build_info.get("cmake", {}).get("board", {})
                board_name = board_info.get("name")
                qualifier = board_info.get("qualifiers", "")
                revision = board_info.get("revision", "")

            if not board_name:
                continue

            board_target = board_name
            if revision:
                board_target = f"{board_target}@{revision}"
            if qualifier:
                board_target = f"{board_target}/{qualifier}"

            with open(edt_pickle_file, "rb") as fh:
                edt = pickle.load(fh)

            board_edts.setdefault(board_name, {})[board_target] = edt

        except Exception as exc:  # noqa: BLE001
            logger.error("Error processing %s: %s", build_info_file, exc)

    return board_edts


# ---------------------------------------------------------------------------
# Database construction
# ---------------------------------------------------------------------------

def _node_location(node, board_name: str) -> str:
    """Return 'board' or 'soc' depending on where the DTS node is defined."""
    filename = node.filename
    if not filename:
        return "soc"
    path = Path(filename)
    if path.is_relative_to(ZEPHYR_BASE):
        rel = path.relative_to(ZEPHYR_BASE)
        if rel.parts[0] == "boards":
            return "board"
    return "soc"


def build_catalog(board_edts: dict) -> dict:
    """Build the DTS catalog from harvested EDT objects.

    Args:
        board_edts: Nested dict ``{board_name: {board_target: edt_object}}``.

    Returns:
        Dict with ``boards`` and ``compatibles`` top-level keys (see module
        docstring for the full schema).
    """
    boards_db = {}
    compatibles_db = {}

    for board_name, targets in board_edts.items():
        board_entry = {"targets": {}}

        for board_target, edt in targets.items():
            target_compatibles = set()
            hardware = {}

            for node in edt.nodes:
                if node.binding_path is None or node.matching_compat is None:
                    continue

                # Skip synthetic "zephyr," nodes (except native_sim where they
                # represent real peripherals).
                if (
                    node.matching_compat.startswith("zephyr,")
                    and board_name != "native_sim"
                ):
                    continue

                binding_path = Path(node.binding_path)
                binding_type = _get_binding_type(binding_path)
                compat = node.matching_compat

                target_compatibles.add(compat)

                # Build human-readable node source location.
                filename = node.filename or ""
                rel_filename = filename
                if filename and Path(filename).is_relative_to(ZEPHYR_BASE):
                    rel_filename = str(Path(filename).relative_to(ZEPHYR_BASE))

                node_info = {"file": rel_filename, "line": node.lineno}
                is_okay = node.status == "okay"

                # Accumulate per-compatible data inside the hardware map.
                compat_entry = hardware.setdefault(binding_type, {}).get(compat)
                if compat_entry is None:
                    compat_entry = {
                        "description": _first_sentence(node.description),
                        "title": node.title or "",
                        "locations": set(),
                        "okay": False,
                        "dts_sources": [],
                    }
                    hardware.setdefault(binding_type, {})[compat] = compat_entry

                compat_entry["locations"].add(_node_location(node, board_name))
                if is_okay:
                    compat_entry["okay"] = True
                compat_entry["dts_sources"].append(node_info)

                # Update global reverse index.
                if compat not in compatibles_db:
                    compatibles_db[compat] = {
                        "description": _first_sentence(node.description),
                        "title": node.title or "",
                        "binding_type": binding_type,
                        "boards": [],
                    }
                if board_name not in compatibles_db[compat]["boards"]:
                    compatibles_db[compat]["boards"].append(board_name)

            # Convert sets → sorted lists for JSON serialisation.
            for btype in hardware:
                for compat_data in hardware[btype].values():
                    compat_data["locations"] = sorted(compat_data["locations"])

            board_entry["targets"][board_target] = {
                "compatibles": sorted(target_compatibles),
                "hardware": hardware,
            }

        boards_db[board_name] = board_entry

    # Sort the reverse-index board lists for deterministic output.
    for entry in compatibles_db.values():
        entry["boards"].sort()

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "zephyr_base": str(ZEPHYR_BASE),
        "boards": boards_db,
        "compatibles": compatibles_db,
    }


def _first_sentence(text: str | None) -> str:
    """Return the first sentence of *text*, or the whole text if none found."""
    if not text:
        return ""
    text = text.replace("\n", " ")
    first_para = text.split("  ")[0].strip()
    import re
    match = re.search(r"(.*?)\.(?:\s|$)", first_para)
    if match:
        return match.group(1).strip()
    return first_para


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="FILE",
        help="Path for the output JSON database.",
    )
    parser.add_argument(
        "--twister-outdir",
        metavar="DIR",
        help=(
            "Directory to use for twister output.  "
            "Defaults to a temporary directory that is deleted after the run."
        ),
    )
    parser.add_argument(
        "--skip-twister",
        action="store_true",
        help=(
            "Skip the twister cmake-only step and use whatever build artefacts "
            "already exist in --twister-outdir.  Implies --twister-outdir."
        ),
    )
    parser.add_argument(
        "--vendor",
        action="append",
        dest="vendor_filter",
        default=[],
        metavar="VENDOR",
        help="Restrict to boards from this vendor (may be repeated).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose/debug logging.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.skip_twister and not args.twister_outdir:
        logger.error("--skip-twister requires --twister-outdir")
        sys.exit(1)

    tmp_dir = None
    try:
        if args.twister_outdir:
            twister_outdir = Path(args.twister_outdir)
            twister_outdir.mkdir(parents=True, exist_ok=True)
        else:
            tmp_dir = tempfile.mkdtemp(prefix="zephyr-dts-catalog-")
            twister_outdir = Path(tmp_dir)

        if not args.skip_twister:
            run_twister_cmake_only(twister_outdir, args.vendor_filter)

        logger.info("Harvesting EDT data from %s …", twister_outdir)
        board_edts = gather_board_edts(twister_outdir)
        logger.info("Loaded EDT data for %d boards", len(board_edts))

        logger.info("Building catalog …")
        catalog = build_catalog(board_edts)

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(catalog, fh, indent=2, default=str)

        n_boards = len(catalog["boards"])
        n_compats = len(catalog["compatibles"])
        logger.info(
            "Wrote %s  (%d boards, %d compatibles)", output_path, n_boards, n_compats
        )

    finally:
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
