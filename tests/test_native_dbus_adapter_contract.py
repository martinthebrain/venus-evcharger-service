# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the native adapter's generated publication surface authoritative."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.dev.export_native_dbus_adapter_contract import (
    contract,
    runtime_policy_contract,
)


class NativeDbusAdapterContractTests(unittest.TestCase):
    def test_checked_in_publication_contract_matches_python_boundary(self) -> None:
        path = Path("rust/dbus-adapter/contracts/publication.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), contract())

    def test_checked_in_runtime_policy_matches_python_behavior(self) -> None:
        path = Path("rust/dbus-adapter/contracts/runtime_policy.json")
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            runtime_policy_contract(),
        )


if __name__ == "__main__":
    unittest.main()
