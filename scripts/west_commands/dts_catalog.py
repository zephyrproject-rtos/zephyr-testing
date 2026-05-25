# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""West extension: dts-catalog — query the DTS hardware catalog.

Subcommands
-----------
  fetch      Download the latest catalog artifact from GitHub Actions.
  stats      Show catalog statistics.
  boards     List boards in the catalog.
  targets    List build targets for a board.
  compat     List or search DT compatible strings.
  who-uses   List boards that use a given compatible.
  hardware   Show hardware entries for a board (use --pretty for a tree view).

The query subcommands (all except ``fetch``) mirror the interface of
``scripts/query_dts_catalog.py`` and accept a ``--catalog`` option to
point at a local JSON file.  When ``--catalog`` is omitted, the command
looks for the file cached by ``west dts-catalog fetch`` (stored at
``~/.cache/zephyr/dts-hardware-catalog.json`` by default).
"""

import argparse
import io
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from west.commands import WestCommand

sys.path.append(os.fspath(Path(__file__).parent.parent))
import query_dts_catalog  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ARTIFACT_PREFIX = "dts-hardware-catalog"
_DEFAULT_REPO = "zephyrproject-rtos/zephyr"
_GH_API = "https://api.github.com"
_DEFAULT_CACHE = Path.home() / ".cache" / "zephyr" / "dts-hardware-catalog.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_catalog(west_cmd):
    """Return the path configured via ``west config`` or the default cache."""
    try:
        cfg = west_cmd.config.get("dts-catalog.catalog")
    except Exception:  # west config unavailable outside a workspace
        cfg = None
    if cfg:
        return Path(cfg)
    return _DEFAULT_CACHE


def _require_catalog(west_cmd, args):
    """Load and return the catalog DB, or die with a helpful message."""
    path = Path(args.catalog) if args.catalog else _default_catalog(west_cmd)
    if not path.exists():
        west_cmd.die(
            f"catalog not found at '{path}'.\n"
            "Run 'west dts-catalog fetch' to download it, "
            "or pass --catalog <file>."
        )
    return query_dts_catalog.load_db(os.fspath(path))


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def _gh_request(url, token):
    """Perform an authenticated GitHub API GET and return parsed JSON."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"GitHub API error {exc.code}: {exc.reason} — {url}"
        ) from exc


def _gh_download(url, token):
    """Download a GitHub artifact ZIP.

    GitHub's archive_download_url returns a 302 redirect to Azure Blob
    Storage.  The redirect target already carries a SAS token in the query
    string, so the Authorization header must NOT be forwarded — Azure rejects
    requests that carry both a SAS token and a Bearer header.

    Strategy: issue the authenticated request with redirect following
    disabled, capture the Location header from the 302, then fetch that
    plain URL without any auth header.
    """
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect())

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    try:
        with opener.open(req) as resp:
            # No redirect: artifact served directly (unlikely but handled).
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get("Location")
            if not location:
                raise RuntimeError(
                    f"GitHub returned {exc.code} but no Location header — {url}"
                ) from exc
            # Fetch the redirect target without auth headers.
            try:
                with urllib.request.urlopen(location) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc2:
                raise RuntimeError(
                    f"Download error {exc2.code}: {exc2.reason} — {location}"
                ) from exc2
        raise RuntimeError(
            f"Download error {exc.code}: {exc.reason} — {url}"
        ) from exc


# ---------------------------------------------------------------------------
# fetch subcommand
# ---------------------------------------------------------------------------


def cmd_fetch(west_cmd, args):
    """Download the most-recent matching artifact and cache it locally."""
    repo = args.repo
    branch = args.branch
    token = args.token or os.environ.get("GITHUB_TOKEN")
    output = Path(args.output) if args.output else _default_catalog(west_cmd)

    if not token:
        west_cmd.die(
            "A GitHub token is required to download artifacts.\n"
            "Provide --token or set the GITHUB_TOKEN environment variable."
        )

    west_cmd.inf(f"Searching for '{_ARTIFACT_PREFIX}-*' in {repo} (branch={branch}) ...")

    # GitHub returns artifacts newest-first; stop at the first valid match.
    best = None
    page = 1
    while best is None:
        url = f"{_GH_API}/repos/{repo}/actions/artifacts?per_page=100&page={page}"
        data = _gh_request(url, token)
        artifacts = data.get("artifacts", [])
        if not artifacts:
            break
        for art in artifacts:
            if not art["name"].startswith(_ARTIFACT_PREFIX):
                continue
            if art.get("expired"):
                continue
            run_info = art.get("workflow_run") or {}
            art_branch = run_info.get("head_branch", "")
            if branch and art_branch and art_branch != branch:
                continue
            best = art
            break
        # No need to fetch further pages if we already found a match, or if
        # there are no more pages.
        if best is not None or len(artifacts) < 100:
            break
        page += 1

    if best is None:
        west_cmd.die(
            f"No unexpired '{_ARTIFACT_PREFIX}-*' artifact found in {repo} "
            f"for branch '{branch}'."
        )

    west_cmd.inf(f"Found: {best['name']} (id={best['id']})")
    west_cmd.inf("Downloading ...")

    raw = _gh_download(best["archive_download_url"], token)

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        json_names = [n for n in zf.namelist() if n.endswith(".json")]
        if not json_names:
            west_cmd.die(
                f"No .json file found in artifact ZIP "
                f"(contents: {zf.namelist()})"
            )
        catalog_bytes = zf.read(json_names[0])

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(catalog_bytes)
    west_cmd.inf(f"Catalog saved to '{output}'.")

    db = json.loads(catalog_bytes)
    n_boards = len(db.get("boards", {}))
    n_compat = len(db.get("compatibles", {}))
    west_cmd.inf(f"  {n_boards} boards, {n_compat} compatibles.")


# ---------------------------------------------------------------------------
# WestCommand
# ---------------------------------------------------------------------------

_QUERY_DISPATCH = {
    "stats": query_dts_catalog.cmd_stats,
    "boards": query_dts_catalog.cmd_boards,
    "targets": query_dts_catalog.cmd_targets,
    "compat": query_dts_catalog.cmd_compat,
    "who-uses": query_dts_catalog.cmd_who_uses,
    "hardware": query_dts_catalog.cmd_hardware,
}


class DtsCatalog(WestCommand):

    def __init__(self):
        super().__init__(
            "dts-catalog",
            "query the DTS hardware catalog",
            description=textwrap.dedent("""\
                Query the Zephyr DTS hardware catalog.

                Use 'west dts-catalog fetch' to download the latest catalog
                generated by the CI workflow, then use the query sub-commands
                to inspect board/compatible relationships.
            """),
            accepts_unknown_args=False,
        )

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(
            self.name,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=self.description,
            epilog=textwrap.dedent(f"""\
                examples:
                  west dts-catalog fetch --branch main
                  west dts-catalog stats
                  west dts-catalog boards --filter nrf
                  west dts-catalog targets nrf52840dk
                  west dts-catalog compat --type serial
                  west dts-catalog who-uses nordic,nrf-uart
                  west dts-catalog hardware nrf52840dk --okay-only
                  west dts-catalog hardware nrf52840dk --pretty
                  west dts-catalog hardware nrf52840dk --pretty --okay-only

                The default catalog path is:
                  {_DEFAULT_CACHE}
                Override globally with:
                  west config dts-catalog.catalog /path/to/dts_catalog.json
            """),
        )

        sub = parser.add_subparsers(dest="subcmd", metavar="SUBCOMMAND")
        sub.required = True

        _catalog_arg = dict(
            metavar="FILE",
            help=(
                f"Path to catalog JSON file (default: {_DEFAULT_CACHE}). "
                "Can also be set via "
                "'west config dts-catalog.catalog <path>'."
            ),
        )

        # -- fetch ------------------------------------------------------------
        p = sub.add_parser(
            "fetch",
            help="Download the latest catalog artifact from GitHub Actions.",
        )
        p.add_argument(
            "--repo",
            default=_DEFAULT_REPO,
            metavar="OWNER/REPO",
            help=f"GitHub repository (default: {_DEFAULT_REPO}).",
        )
        p.add_argument(
            "--branch",
            default="main",
            help="Branch to look for artifacts on (default: main).",
        )
        p.add_argument(
            "--token",
            metavar="TOKEN",
            help="GitHub personal access token (or set GITHUB_TOKEN).",
        )
        p.add_argument(
            "--output",
            "-o",
            metavar="FILE",
            help=f"Output path (default: {_DEFAULT_CACHE}).",
        )

        # -- stats ------------------------------------------------------------
        p = sub.add_parser("stats", help="Show catalog statistics.")
        p.add_argument("--catalog", **_catalog_arg)
        p.add_argument("--json", action="store_true", help="Output as JSON.")

        # -- boards -----------------------------------------------------------
        p = sub.add_parser("boards", help="List boards in the catalog.")
        p.add_argument("--catalog", **_catalog_arg)
        p.add_argument(
            "--filter",
            "-f",
            metavar="PATTERN",
            help="Show only boards whose name contains PATTERN.",
        )
        p.add_argument("--json", action="store_true", help="Output as JSON.")

        # -- targets ----------------------------------------------------------
        p = sub.add_parser("targets", help="List build targets for a board.")
        p.add_argument("--catalog", **_catalog_arg)
        p.add_argument("board", metavar="BOARD")
        p.add_argument("--json", action="store_true", help="Output as JSON.")

        # -- compat -----------------------------------------------------------
        p = sub.add_parser("compat", help="List or search DT compatible strings.")
        p.add_argument("--catalog", **_catalog_arg)
        p.add_argument(
            "--filter",
            "-f",
            metavar="PATTERN",
            help="Show only compatibles whose name contains PATTERN.",
        )
        p.add_argument(
            "--type",
            "-t",
            metavar="TYPE",
            dest="type",
            help="Filter by binding type (e.g. serial, gpio, sensor).",
        )
        p.add_argument("--json", action="store_true", help="Output as JSON.")

        # -- who-uses ---------------------------------------------------------
        p = sub.add_parser(
            "who-uses",
            help="List boards that use a given compatible string.",
        )
        p.add_argument("--catalog", **_catalog_arg)
        p.add_argument("compatible", metavar="COMPATIBLE")
        p.add_argument(
            "--partial",
            "-p",
            action="store_true",
            help="Treat COMPATIBLE as a substring pattern.",
        )
        p.add_argument("--json", action="store_true", help="Output as JSON.")

        # -- hardware ---------------------------------------------------------
        p = sub.add_parser(
            "hardware",
            help="Show hardware entries for a board.",
        )
        p.add_argument("--catalog", **_catalog_arg)
        p.add_argument("board", metavar="BOARD")
        p.add_argument(
            "--target",
            metavar="TARGET",
            help="Board target (default: first available).",
        )
        p.add_argument(
            "--type",
            "-t",
            metavar="TYPE",
            dest="type",
            help="Filter by binding type (e.g. serial, gpio, sensor).",
        )
        p.add_argument(
            "--okay-only",
            "-O",
            action="store_true",
            help="Show only nodes with status = okay.",
        )
        p.add_argument("--json", action="store_true", help="Output as JSON.")
        p.add_argument(
            "--pretty",
            "-P",
            action="store_true",
            help=(
                "Render output as a structured ASCII tree with type sections "
                "and visual indicators. Ignored when --json is set."
            ),
        )

        return parser

    def do_run(self, args, _unknown):
        if args.subcmd == "fetch":
            try:
                cmd_fetch(self, args)
            except RuntimeError as exc:
                self.die(str(exc))
            return

        db = _require_catalog(self, args)
        _QUERY_DISPATCH[args.subcmd](db, args)
