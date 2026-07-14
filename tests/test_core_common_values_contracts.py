# SPDX-License-Identifier: GPL-3.0-or-later
"""Value and conversion contracts for the shared core helpers."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from venus_evcharger.core.common_values import (
    mode_uses_auto_logic,
    mode_uses_scheduled_logic,
    normalize_mode,
    normalize_phase,
    phase_values,
    read_version,
)


class TestCoreCommonValuesContracts(unittest.TestCase):
    def test_read_version_prefers_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_dir = root / "venus_evcharger" / "core"
            module_dir.mkdir(parents=True)
            (root / "version.txt").write_text("version:2.3.4\nignored\n", encoding="utf-8")
            (module_dir / "version.txt").write_text("version: 1.0.0\n", encoding="utf-8")
            with patch("venus_evcharger.core.common_values.__file__", str(module_dir / "common_values.py")):
                self.assertEqual(read_version("version.txt"), "2.3.4")

    def test_read_version_uses_module_fallback_and_last_colon_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_dir = root / "venus_evcharger" / "core"
            module_dir.mkdir(parents=True)
            (module_dir / "version.txt").write_text("build: version: 4.5.6 \n", encoding="utf-8")
            with patch("venus_evcharger.core.common_values.__file__", str(module_dir / "common_values.py")):
                self.assertEqual(read_version("version.txt"), "4.5.6")

    def test_read_version_missing_file_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module_file = Path(temp_dir) / "package" / "common_values.py"
            module_file.parent.mkdir()
            with patch("venus_evcharger.core.common_values.__file__", str(module_file)), self.assertLogs(
                level="WARNING"
            ) as logs:
                self.assertEqual(read_version("missing.txt"), "0.1")
        self.assertEqual(logs.output, ["WARNING:root:File missing.txt not found in the service root."])

    def test_read_version_opens_explicit_utf8_text_stream(self) -> None:
        module_file = "/service/venus_evcharger/core/common_values.py"
        opened = mock_open(read_data="version: 7.8.9\n")
        with patch("venus_evcharger.core.common_values.__file__", module_file), patch(
            "builtins.open", opened
        ):
            self.assertEqual(read_version("version.txt"), "7.8.9")
        opened.assert_called_once_with("/service/version.txt", "r", encoding="utf-8")

    def test_three_phase_phase_voltage_contract(self) -> None:
        result = phase_values(6900, 230, "3P", "phase")
        expected = {"power": 2300.0, "voltage": 230.0, "current": 10.0}
        self.assertEqual(result, {"L1": expected, "L2": expected, "L3": expected})
        self.assertIsNot(result["L1"], result["L2"])

    def test_three_phase_line_voltage_contract(self) -> None:
        result = phase_values("6900", "400", "3P", "line")
        phase_voltage = 400.0 / math.sqrt(3)
        expected_current = 2300.0 / phase_voltage
        for phase in ("L1", "L2", "L3"):
            with self.subTest(phase=phase):
                self.assertAlmostEqual(result[phase]["power"], 2300.0)
                self.assertAlmostEqual(result[phase]["voltage"], phase_voltage)
                self.assertAlmostEqual(result[phase]["current"], expected_current)

    def test_single_phase_contract_for_every_phase(self) -> None:
        for active_phase in ("L1", "L2", "L3"):
            with self.subTest(active_phase=active_phase):
                result = phase_values(2300, 230, active_phase, "phase")
                self.assertEqual(set(result), {"L1", "L2", "L3"})
                for phase in ("L1", "L2", "L3"):
                    expected_power = 2300.0 if phase == active_phase else 0.0
                    expected_current = 10.0 if phase == active_phase else 0.0
                    self.assertEqual(
                        result[phase],
                        {"power": expected_power, "voltage": 230.0, "current": expected_current},
                    )

    def test_phase_values_reject_zero_voltage(self) -> None:
        for voltage in (0, 0.0, "0"):
            with self.subTest(voltage=voltage):
                with self.assertRaises(ValueError) as raised:
                    phase_values(1000, voltage, "L1", "phase")
                self.assertEqual(raised.exception.args, ("Invalid voltage",))

    def test_normalize_phase_contract(self) -> None:
        expected = {
            "1P": "L1",
            " 1p ": "L1",
            "l1": "L1",
            "L2": "L2",
            "l3": "L3",
            "3p": "3P",
        }
        for value, phase in expected.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_phase(value), phase)
        for invalid in (None, "", "L4", "2P"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "Invalid Phase"):
                normalize_phase(invalid)

    def test_mode_contracts(self) -> None:
        for value, expected in ((0, 0), ("1", 1), (2.0, 2), (3, 0), (-1, 0), (None, 0), ("bad", 0)):
            with self.subTest(value=value):
                self.assertEqual(normalize_mode(value), expected)
        self.assertFalse(mode_uses_auto_logic(0))
        self.assertTrue(mode_uses_auto_logic(1))
        self.assertTrue(mode_uses_auto_logic("2"))
        self.assertFalse(mode_uses_auto_logic(3))
        self.assertFalse(mode_uses_scheduled_logic(0))
        self.assertFalse(mode_uses_scheduled_logic(1))
        self.assertTrue(mode_uses_scheduled_logic("2"))
        self.assertFalse(mode_uses_scheduled_logic(3))


if __name__ == "__main__":
    unittest.main()
