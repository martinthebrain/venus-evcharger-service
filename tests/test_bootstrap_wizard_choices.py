# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from venus_evcharger.bootstrap.wizard_choices import optional_choice, recognized_choice


class BootstrapWizardChoiceTests(unittest.TestCase):
    def test_recognized_choice_requires_exact_allowed_value(self) -> None:
        allowed = ("manual", "auto", "scheduled")
        self.assertEqual(recognized_choice("manual", allowed), "manual")
        self.assertEqual(recognized_choice("auto", allowed), "auto")
        self.assertEqual(recognized_choice("scheduled", allowed), "scheduled")
        self.assertIsNone(recognized_choice(" Manual ", allowed))
        self.assertIsNone(recognized_choice("missing", allowed))
        self.assertIsNone(recognized_choice(None, allowed))

    def test_optional_choice_contract(self) -> None:
        allowed = ("tcp", "serial_rtu")
        self.assertIsNone(optional_choice(None, allowed, "transport"))
        self.assertEqual(optional_choice("tcp", allowed, "transport"), "tcp")
        self.assertEqual(optional_choice("serial_rtu", allowed, "transport"), "serial_rtu")

        with self.assertRaises(ValueError) as unsupported:
            optional_choice("udp", allowed, "transport")
        self.assertEqual(str(unsupported.exception), "Unsupported transport: udp")

    def test_choice_preserves_first_exact_duplicate(self) -> None:
        allowed = ("first", "first", "second")
        self.assertEqual(recognized_choice("first", allowed), "first")
        self.assertEqual(optional_choice("second", allowed, "label"), "second")


if __name__ == "__main__":
    unittest.main()
