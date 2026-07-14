# SPDX-License-Identifier: GPL-3.0-or-later
"""Display contracts for shared EV-charger value types."""

from __future__ import annotations

import unittest

from venus_evcharger.core.common_types import _status_label


class TestCoreCommonTypesContracts(unittest.TestCase):
    def test_status_labels_cover_every_public_code(self) -> None:
        expected = {
            0: "Getrennt",
            1: "Bereit",
            2: "Laden",
            3: "Fertig",
            4: "Warten auf PV",
            6: "Warten auf Start",
        }
        for code, label in expected.items():
            with self.subTest(code=code):
                self.assertEqual(_status_label(None, code), label)
                self.assertEqual(_status_label(None, str(code)), label)
        self.assertEqual(_status_label(None, 5), "Unbekannt")


if __name__ == "__main__":
    unittest.main()
