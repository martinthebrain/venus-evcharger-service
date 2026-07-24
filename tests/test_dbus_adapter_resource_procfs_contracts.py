# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux procfs resource-reader contracts."""

from __future__ import annotations

import math
import os
import unittest
from unittest.mock import mock_open, patch

from venus_evcharger.dbus_adapter.resource_procfs import ProcfsResourceReader


class TestProcfsResourceReaderContracts(unittest.TestCase):
    def test_cpu_and_memory_sources_use_exact_procfs_paths(self) -> None:
        reader = ProcfsResourceReader(123)
        system_open = mock_open(read_data="cpu 1 2 3 4 5 6\n")
        with patch("builtins.open", system_open):
            self.assertEqual(reader.system_cpu(), (21, 9))
        system_open.assert_called_once_with("/proc/stat", encoding="utf-8")

        fields_from_state = ["S", *(["0"] * 12)]
        fields_from_state[11], fields_from_state[12] = "10", "20"
        process_open = mock_open(
            read_data=f"123 (worker name) {' '.join(fields_from_state)}"
        )
        with patch("builtins.open", process_open), patch(
            "venus_evcharger.dbus_adapter.resource_procfs.os.sysconf",
            return_value=100,
        ) as sysconf:
            self.assertEqual(reader.process_cpu_seconds(), 0.3)
        process_open.assert_called_once_with("/proc/123/stat", encoding="utf-8")
        sysconf.assert_called_once_with(os.sysconf_names["SC_CLK_TCK"])

        mem_open = mock_open(read_data="MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
        with patch("builtins.open", mem_open):
            self.assertEqual(reader.meminfo(), {"MemTotal": 1000.0, "MemAvailable": 250.0})
        mem_open.assert_called_once_with("/proc/meminfo", encoding="utf-8")

        with patch("builtins.open", mock_open(read_data="MemAvailable: 250.5 kB\n")):
            self.assertEqual(reader.meminfo(), {"MemAvailable": 250.5})

        status_open = mock_open(read_data="VmRSS: 10 kB\nThreads: 3\nName: python\n")
        with patch("builtins.open", status_open):
            self.assertEqual(reader.process_status(), {"VmRSS": 10.0, "Threads": 3.0})
        status_open.assert_called_once_with("/proc/123/status", encoding="utf-8")

    def test_venus_status_empty_groups_and_malformed_values_are_ignored(self) -> None:
        status = (
            "Name:\tpython3\n"
            "Groups:\t \n"
            "FDSize:\t256\n"
            "VmHWM:\t21764 kB\n"
            "VmRSS:\tbad kB\n"
            "Threads:\t1\n"
        )
        with patch("builtins.open", mock_open(read_data=status)):
            self.assertEqual(
                ProcfsResourceReader(1481).process_status(),
                {"FDSize": 256.0, "VmHWM": 21764.0, "Threads": 1.0},
            )

    def test_capture_failures_are_explicitly_unavailable(self) -> None:
        reader = ProcfsResourceReader(123)
        with patch("builtins.open", side_effect=OSError("missing")):
            self.assertIsNone(reader.system_cpu())
            self.assertIsNone(reader.process_cpu_seconds())
            self.assertIsNone(reader.meminfo())
            self.assertIsNone(reader.process_status())
        with patch("builtins.open", mock_open(read_data="short")):
            self.assertIsNone(reader.process_cpu_seconds())
        with patch("builtins.open", mock_open(read_data="cpu invalid\n")):
            self.assertIsNone(reader.system_cpu())
        with patch("builtins.open", mock_open(read_data="broken\n")):
            self.assertIsNone(reader.meminfo())
        with patch("builtins.open", mock_open(read_data="Key: not-a-number\n")):
            self.assertIsNone(reader.meminfo())

    def test_cpu_sources_reject_nonphysical_values(self) -> None:
        reader = ProcfsResourceReader(123)
        for raw in ("", "cpu 0 0 0 0\n", "cpu -1 0 0 2 0\n", "cpu -1 0 0 0\n"):
            with self.subTest(raw=raw), patch("builtins.open", mock_open(read_data=raw)):
                self.assertIsNone(reader.system_cpu())

        for raw, expected in (
            ("cpu 1 0 0 0\n", (1, 0)),
            ("cpu 0 0 0 1\n", (1, 1)),
        ):
            with self.subTest(raw=raw), patch("builtins.open", mock_open(read_data=raw)):
                self.assertEqual(reader.system_cpu(), expected)

        fields_from_state = ["S", *(["0"] * 12)]
        fields_from_state[11], fields_from_state[12] = "nan", "0"
        with patch(
            "builtins.open",
            mock_open(read_data=f"123 (worker) {' '.join(fields_from_state)}"),
        ), patch(
            "venus_evcharger.dbus_adapter.resource_procfs.os.sysconf",
            return_value=100,
        ):
            self.assertIsNone(reader.process_cpu_seconds())

        fields_from_state[11] = "0"
        with patch(
            "builtins.open",
            mock_open(read_data=f"123 (worker) {' '.join(fields_from_state)}"),
        ), patch(
            "venus_evcharger.dbus_adapter.resource_procfs.os.sysconf",
            return_value=100,
        ):
            self.assertEqual(reader.process_cpu_seconds(), 0.0)

        fields_from_state[11] = "-1"
        with patch(
            "builtins.open",
            mock_open(read_data=f"123 (worker) {' '.join(fields_from_state)}"),
        ), patch(
            "venus_evcharger.dbus_adapter.resource_procfs.os.sysconf",
            return_value=100,
        ):
            self.assertIsNone(reader.process_cpu_seconds())

    def test_host_values_and_fd_count_report_unavailability(self) -> None:
        reader = ProcfsResourceReader(123)
        with patch("venus_evcharger.dbus_adapter.resource_procfs.os.getloadavg", return_value=(1, 2, 3)):
            self.assertEqual(reader.load_average(), (1.0, 2.0, 3.0))
        with patch("venus_evcharger.dbus_adapter.resource_procfs.os.getloadavg", side_effect=OSError):
            self.assertIsNone(reader.load_average())
        with patch(
            "venus_evcharger.dbus_adapter.resource_procfs.os.getloadavg",
            return_value=(math.nan, 2, 3),
        ):
            self.assertIsNone(reader.load_average())
        for loads in ((math.inf, 2, 3), (-1, 2, 3)):
            with self.subTest(loads=loads), patch(
                "venus_evcharger.dbus_adapter.resource_procfs.os.getloadavg",
                return_value=loads,
            ):
                self.assertIsNone(reader.load_average())
        with patch(
            "venus_evcharger.dbus_adapter.resource_procfs.os.getloadavg",
            return_value=(0, 0, 0),
        ):
            self.assertEqual(reader.load_average(), (0.0, 0.0, 0.0))
        with patch("venus_evcharger.dbus_adapter.resource_procfs.os.cpu_count", return_value=4):
            self.assertEqual(reader.cpu_count(), 4)
        with patch("venus_evcharger.dbus_adapter.resource_procfs.os.cpu_count", return_value=None):
            self.assertEqual(reader.cpu_count(), 1)
        with patch("venus_evcharger.dbus_adapter.resource_procfs.os.listdir", return_value=["0", "1", "2"]):
            self.assertEqual(reader.open_fd_count(), 3)
        with patch("venus_evcharger.dbus_adapter.resource_procfs.os.listdir", side_effect=OSError):
            self.assertIsNone(reader.open_fd_count())


if __name__ == "__main__":
    unittest.main()
