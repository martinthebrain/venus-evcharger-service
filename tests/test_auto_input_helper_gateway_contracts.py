# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.support.auto_input_helper import FakeGatewayClient, helper_settings
from venus_evcharger.inputs.helper.energy_gateway import GatewayEnergySnapshots
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergySourceDescriptor,
    EnergyTopologySnapshot,
    MeasuredValue,
)


def _inputs() -> EnergyInputsSnapshot:
    return EnergyInputsSnapshot(
        sequence=2,
        captured_at=100.0,
        topology_generation=3,
        grid_power_w=MeasuredValue(-20.0, 99.0, "fresh", 1.0),
        pv_power_w=MeasuredValue(500.0, 99.0, "fresh", 0.9, ("pv-ac-a",)),
        battery_soc=MeasuredValue(72.0, 98.0, "stale", 0.8, ("battery-a",)),
    )


def _topology() -> EnergyTopologySnapshot:
    return EnergyTopologySnapshot(
        generation=3,
        captured_at=100.0,
        sources=(EnergySourceDescriptor("pv-ac-a", "pv_ac", "online", ("power",)),),
    )


class AutoInputHelperGatewayContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeGatewayClient()
        self.reader = GatewayEnergySnapshots(helper_settings(), self.client)

    def test_loads_typed_inputs_topology_and_measurements(self) -> None:
        self.client.inputs = _inputs()
        self.client.topology = _topology()
        self.assertIs(self.reader.refresh_inputs(), self.client.inputs)
        self.assertIs(self.reader.refresh_topology(), self.client.topology)
        self.assertEqual(self.reader.measurement("pv"), self.client.inputs.pv_power_w)
        self.assertEqual(self.reader.measurement("grid"), self.client.inputs.grid_power_w)
        self.assertEqual(self.reader.measurement("battery"), self.client.inputs.battery_soc)
        self.assertIsNone(self.reader.measurement("topology"))

    def test_missing_snapshot_and_reset_are_explicit(self) -> None:
        self.assertIsNone(self.reader.refresh_inputs())
        self.assertIsNone(self.reader.refresh_topology())
        self.assertIsNone(self.reader.measurement("pv"))
        self.reader.reset()
        self.assertIsNone(self.reader.measurement("battery"))

    def test_refresh_uses_typed_scope_and_rate_limits_duplicates(self) -> None:
        with patch("venus_evcharger.inputs.helper.energy_gateway.time.monotonic", side_effect=(10.0, 11.0, 41.0)):
            self.assertTrue(self.reader.request_refresh("pv", reason="missing", priority=True))
            self.assertFalse(self.reader.request_refresh("pv", reason="duplicate", priority=True))
            self.assertTrue(self.reader.request_refresh("pv", reason="retry"))
        first, source = self.client.refresh_requests[0]
        self.assertEqual(source, "auto-input-helper")
        self.assertEqual(first.scope, "pv")
        self.assertEqual(first.urgency, "priority")
        self.assertEqual(first.max_age_seconds, self.reader.settings.gateway_max_age_seconds)

    def test_topology_refresh_uses_topology_age_and_errors_remain_best_effort(self) -> None:
        with patch("venus_evcharger.inputs.helper.energy_gateway.time.monotonic", return_value=10.0):
            self.assertTrue(self.reader.request_refresh("topology", reason="periodic"))
        request = self.client.refresh_requests[-1][0]
        self.assertEqual(request.max_age_seconds, self.reader.settings.topology_refresh_seconds)
        self.client.error = OSError("mailbox unavailable")
        with patch("venus_evcharger.inputs.helper.energy_gateway.time.monotonic", return_value=100.0):
            self.assertFalse(self.reader.request_refresh("battery", reason="missing"))


if __name__ == "__main__":
    unittest.main()
