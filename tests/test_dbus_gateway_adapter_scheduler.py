# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable test entry point for split DBus gateway adapter scenarios.

The concrete cases live in small responsibility-focused support modules.
This class keeps historical unittest and mutation-audit node IDs stable.
"""

from __future__ import annotations

from tests.support import dbus_gateway_adapter_cases as adapter_cases


class DbusGatewayAdapterSchedulerTests(adapter_cases.AllGatewayAdapterCases):
    """Collect the responsibility-focused gateway adapter contract cases."""


if __name__ == "__main__":
    import unittest

    unittest.main()
