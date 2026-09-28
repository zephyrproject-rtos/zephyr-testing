# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Production of Doxygen XML snapshots for a given revision.

Zephyr's own doc/zephyr.doxyfile.in is reused rather than a hand-written
Doxyfile, so that a snapshot sees exactly the input set, exclusions and
PREDEFINED macros that the documentation build sees. The template only
interpolates four variables, so it can be driven without CMake.

A revision is materialized with a git worktree rather than by checking
anything out, so the working tree is never disturbed.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .gitutil import git

#: Overrides appended after the template. Doxygen honours the last assignment
#: of a tag, so these win. XML is all that is wanted here, as fast as possible.
_OVERRIDES = """
GENERATE_HTML          = NO
GENERATE_LATEX         = NO
GENERATE_XML           = YES
XML_PROGRAMLISTING     = NO
XML_NS_MEMB_FILE_SCOPE = NO
HAVE_DOT               = NO
QUIET                  = YES
WARNINGS               = NO
WARN_IF_UNDOCUMENTED   = NO
WARN_IF_DOC_ERROR      = NO
WARN_AS_ERROR          = NO
"""

TEMPLATE = Path("doc") / "zephyr.doxyfile.in"


class DoxygenError(RuntimeError):
    pass


def render_doxyfile(zephyr_base: Path, out_dir: Path, version: str = "0.0.0") -> str:
    """Fill in the template for a tree rooted at zephyr_base."""
    template = zephyr_base / TEMPLATE
    if not template.exists():
        raise DoxygenError(f"missing Doxyfile template at {template}")

    text = template.read_text(encoding="utf-8")
    for name, value in (
        ("@ZEPHYR_BASE@", str(zephyr_base)),
        ("@DOXY_OUT@", str(out_dir)),
        ("@ZEPHYR_VERSION@", version),
        ("@INCLUDE_CUSTOM_FILE@", ""),
    ):
        text = text.replace(name, value)
    return text + _OVERRIDES


def build_xml(zephyr_base: Path, out_dir: Path, version: str = "0.0.0") -> Path:
    """Run Doxygen over a tree and return the resulting xml directory."""
    if shutil.which("doxygen") is None:
        raise DoxygenError("doxygen not found on PATH")

    zephyr_base = Path(zephyr_base).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    doxyfile = out_dir / "Doxyfile"
    doxyfile.write_text(render_doxyfile(zephyr_base, out_dir, version), encoding="utf-8")

    result = subprocess.run(
        ("doxygen", str(doxyfile)),
        cwd=zephyr_base,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DoxygenError(f"doxygen failed:\n{result.stderr[-2000:]}")

    xml_dir = out_dir / "xml"
    if not (xml_dir / "index.xml").exists():
        raise DoxygenError(f"doxygen produced no XML index in {xml_dir}")
    return xml_dir


def snapshot_rev(repo: Path, rev: str, out_dir: Path) -> tuple[Path, Path]:
    """Build a Doxygen XML snapshot of a revision.

    Returns (xml_dir, source_root). The source root is the temporary worktree
    and no longer exists once this returns, but declaration paths recorded in
    the XML are relative to it, so it is needed to make them repo-relative.
    """
    repo = Path(repo).resolve()
    with tempfile.TemporaryDirectory(prefix="api-compat-") as tmp:
        worktree = Path(tmp) / "tree"
        git("worktree", "add", "--detach", "--quiet", str(worktree), rev, cwd=repo)
        try:
            xml_dir = build_xml(worktree, Path(out_dir))
        finally:
            # Always detach the worktree, even if Doxygen failed, so that the
            # repository is not left with a stale registration.
            git("worktree", "remove", "--force", str(worktree), cwd=repo)
        return (xml_dir, worktree)
