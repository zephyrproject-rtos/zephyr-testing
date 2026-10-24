# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Entry point for "python -m api_compat"."""

import sys

from .cli import main

sys.exit(main())
