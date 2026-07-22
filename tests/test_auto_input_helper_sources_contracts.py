# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.support.auto_input_helper import FakeEnergyGateway, helper_settings
from venus_evcharger.inputs.helper.sources import AutoInputSources, empty_battery_snapshot
from venus_evcharger.ipc.energy import MeasuredValue


class AutoInputHelperSourceContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = FakeEnergyGateway()
        self.sources = AutoInputSources(helper_settings(), self.gateway)

    def test_one_cycle_projects_all_semantic_measurements(self) -> None:
        self.gateway.measurements = {
            "pv": MeasuredValue(1200.0, 99.0, "fresh", 0.9, ("pv-ac-a", "pv-dc-a")),
            "grid": MeasuredValue(-300.0, 98.0, "stale", 0.8, ("grid-primary",)),
            "battery": MeasuredValue(72.5, 97.0, "fresh", 0.75, ("battery-a", "battery-b")),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            self.sources.prepare_cycle()
            self.assertEqual(self.sources.pv_power(), 1200.0)
            self.assertEqual(self.sources.grid_power(), -300.0)
            battery = self.sources.battery_snapshot()
        self.assertEqual(self.gateway.input_refreshes, 1)
        self.assertEqual(self.sources.observed_at("pv"), 99.0)
        self.assertEqual(self.sources.observed_at("grid"), 98.0)
        self.assertEqual(self.sources.observed_at("battery"), 97.0)
        self.assertEqual(battery["battery_soc"], 72.5)
        self.assertEqual(battery["battery_source_count"], 2)
        self.assertEqual(battery["battery_average_confidence"], 0.75)
        self.assertEqual(self.gateway.requests, [])

    def test_missing_error_and_expired_values_request_only_semantic_scopes(self) -> None:
        self.gateway.measurements = {
            "pv": MeasuredValue(None, 0.0, "unknown", 0.0),
            "grid": MeasuredValue(10.0, 1.0, "stale", 0.5),
            "battery": MeasuredValue(120.0, 99.0, "fresh", 1.0),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            self.sources.prepare_cycle()
            self.assertIsNone(self.sources.pv_power())
            self.assertIsNone(self.sources.grid_power())
            self.assertEqual(self.sources.battery_snapshot(), empty_battery_snapshot())
        self.assertEqual([request[0] for request in self.gateway.requests], ["pv", "grid", "battery"])
        self.assertTrue(all(request[2] for request in self.gateway.requests))

    def test_unprepared_and_non_data_scopes_are_safe(self) -> None:
        self.assertIsNone(self.sources.pv_power())
        self.assertIsNone(self.sources.observed_at("unknown"))
        self.assertEqual(self.gateway.requests[0][0], "pv")


if __name__ == "__main__":
    unittest.main()
