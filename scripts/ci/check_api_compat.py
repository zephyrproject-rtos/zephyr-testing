#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Standalone entry point for the API compatibility checks.

Examples:

    # Check the changes on a branch against upstream.
    ./scripts/ci/check_api_compat.py check -c origin/main..

    # Show the lifecycle state of every API group in the tree.
    ./scripts/ci/check_api_compat.py audit --summary

    # Compare two Doxygen XML trees symbol by symbol.
    ./scripts/ci/check_api_compat.py signature base/xml head/xml

See scripts/ci/api_compat/README.md for the full picture.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api_compat.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
