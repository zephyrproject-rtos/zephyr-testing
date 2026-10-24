# SPDX-FileCopyrightText: Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""Zephyr API compatibility and lifecycle tooling.

This package inspects changes to public API headers and reports changes that
break the promises made by an API's documented lifecycle state.

The lifecycle state is not stored as a dedicated tag anywhere in the tree. It
is encoded as a semantic version on the owning Doxygen group, as described in
doc/develop/api/overview.rst and doc/develop/api/api_lifecycle.rst:

    0.1.z          experimental, may change or disappear at any time
    0.y.z (y > 1)  unstable, may still change without announcement
    x.y.z (x >= 1) stable, backwards compatibility is expected

Because that state lives on the group rather than on individual symbols, the
same textual change can be routine churn or a contract violation depending on
which group encloses it. Deciding that requires resolving every symbol to its
enclosing group, which is what apidoc.py does.

The package is usable standalone (see __main__.py) and is deliberately free of
any dependency on scripts/ci/check_compliance.py, so that it can later be
wrapped by a ComplianceTest without restructuring.
"""

__version__ = "0.1.0"
