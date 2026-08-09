# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tests.support.auto_input_helper import FakeGatewayClient, helper_settings
from venus_evcharger.inputs.helper.energy_gateway import GatewayEnergySnapshots
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergySourceDescriptor,
    EnergyTopologySnapshot,
    MeasuredValue,
)
from venus_evcharger.ipc.enqueue_result import GatewayEnqueueResult


def _inputs() -> EnergyInputsSnapshot:
    return EnergyInputsSnapshot(
        sequence=2,
        captured_at=100.0,
        topology_generation=3,
        grid_power_w=MeasuredValue(-20.0, 99.0, "fresh", 1.0),
        pv_power_w=MeasuredValue(500.0, 99.0, "fresh", 0.9, ("pv-ac-a",)),
        battery_soc=MeasuredValue(72.0, 98.0, "stale", 0.8, ("battery-a",)),
        battery_net_power_w=MeasuredValue(-800.0, 99.0, "fresh", 0.9, ("battery-a",)),
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

    def test_initial_state_is_empty_and_has_no_throttle_history(self) -> None:
        self.assertIsNone(self.reader._inputs)
        self.assertIsNone(self.reader._topology)
        self.assertEqual(self.reader._request_after, {})

    def test_loads_typed_inputs_topology_and_measurements(self) -> None:
        self.client.inputs = _inputs()
        self.client.topology = _topology()
        self.assertIs(self.reader.refresh_inputs(), self.client.inputs)
        self.assertIs(self.reader.refresh_topology(), self.client.topology)
        self.assertEqual(self.reader.measurement("pv"), self.client.inputs.pv_power_w)
        self.assertEqual(self.reader.measurement("grid"), self.client.inputs.grid_power_w)
        self.assertEqual(self.reader.measurement("battery"), self.client.inputs.battery_soc)
        self.assertEqual(
            self.reader.measurement("battery_power"),
            self.client.inputs.battery_net_power_w,
        )
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

    def test_default_client_receives_the_complete_gateway_path_contract(self) -> None:
        settings = helper_settings()
        with patch("venus_evcharger.inputs.helper.energy_gateway.GatewayClient") as client_type:
            reader = GatewayEnergySnapshots(settings)
        client_type.assert_called_once_with(settings.gateway_paths)
        self.assertIs(reader._client, client_type.return_value)

    def test_snapshot_loaders_use_exact_age_contracts_and_replace_cached_values(self) -> None:
        settings = helper_settings()
        client = MagicMock()
        client.load_energy_inputs.side_effect = (_inputs(), None)
        client.load_energy_topology.side_effect = (_topology(), None)
        reader = GatewayEnergySnapshots(settings, client)

        first_inputs = reader.refresh_inputs()
        first_topology = reader.refresh_topology()
        self.assertEqual(first_inputs, _inputs())
        self.assertEqual(first_topology, _topology())
        self.assertIsNone(reader.refresh_inputs())
        self.assertIsNone(reader.refresh_topology())
        self.assertEqual(
            client.load_energy_inputs.call_args_list[0].kwargs,
            {"max_age_seconds": settings.gateway_max_age_seconds},
        )
        self.assertEqual(
            client.load_energy_topology.call_args_list[0].kwargs,
            {"max_age_seconds": settings.topology_refresh_seconds * 2.0},
        )
        self.assertIsNone(reader.measurement("pv"))

    def test_refresh_request_has_exact_wire_contract_and_scope_local_throttle(self) -> None:
        settings = helper_settings()
        client = MagicMock()
        client.request_energy_refresh.side_effect = (
            GatewayEnqueueResult(True, "one", "mailbox"),
            GatewayEnqueueResult(False, reason="backpressure"),
            GatewayEnqueueResult(True, "three", "socket"),
        )
        reader = GatewayEnergySnapshots(settings, client)

        with (
            patch(
                "venus_evcharger.inputs.helper.energy_gateway.time.monotonic",
                side_effect=(10.0, 10.0, 40.0, 40.0),
            ),
            patch(
                "venus_evcharger.inputs.helper.energy_gateway.uuid.uuid4",
            ) as uuid4,
        ):
            uuid4.return_value.hex = "request-id"
            self.assertTrue(reader.request_refresh("pv", reason="missing", priority=True))
            self.assertFalse(reader.request_refresh("grid", reason="stale"))
            self.assertTrue(reader.request_refresh("pv", reason="boundary"))
            self.assertFalse(reader.request_refresh("pv", reason="duplicate"))

        first = client.request_energy_refresh.call_args_list[0]
        self.assertEqual(first.kwargs, {"source": "auto-input-helper"})
        self.assertEqual(
            first.args[0],
            EnergyRefreshRequest(
                request_id="request-id",
                scope="pv",
                max_age_seconds=settings.gateway_max_age_seconds,
                urgency="priority",
                reason="missing",
            ),
        )
        second = client.request_energy_refresh.call_args_list[1].args[0]
        self.assertEqual(second.scope, "grid")
        self.assertEqual(second.urgency, "normal")
        self.assertEqual(second.reason, "stale")
        third = client.request_energy_refresh.call_args_list[2].args[0]
        self.assertEqual(third.reason, "boundary")

    def test_first_refresh_before_one_monotonic_second_is_allowed_and_uses_canonical_key(self) -> None:
        with patch(
            "venus_evcharger.inputs.helper.energy_gateway.time.monotonic",
            return_value=0.5,
        ):
            self.assertTrue(self.reader.request_refresh("pv", reason="startup"))
        self.assertEqual(
            self.reader._request_after,
            {("pv", ""): 0.5 + self.reader.settings.gateway_error_retry_seconds},
        )

    def test_every_declared_refresh_transport_error_is_best_effort(self) -> None:
        for error in (
            OSError("os"),
            RuntimeError("runtime"),
            TypeError("type"),
            ValueError("value"),
        ):
            with self.subTest(error=type(error).__name__):
                client = MagicMock()
                client.request_energy_refresh.side_effect = error
                reader = GatewayEnergySnapshots(helper_settings(), client)
                with (
                    patch(
                        "venus_evcharger.inputs.helper.energy_gateway.time.monotonic",
                        return_value=10.0,
                    ),
                    patch("venus_evcharger.inputs.helper.energy_gateway.logging.debug") as debug,
                ):
                    self.assertFalse(reader.request_refresh("battery", reason="missing"))
                debug.assert_called_once_with(
                    "Energy refresh request failed scope=%s: %s",
                    "battery",
                    error,
                )

    def test_reset_clears_snapshots_topology_and_all_throttle_keys(self) -> None:
        self.reader._inputs = _inputs()
        self.reader._topology = _topology()
        self.reader._request_after = {("pv", ""): 20.0, ("grid", ""): 30.0}
        self.reader.reset()
        self.assertIsNone(self.reader._inputs)
        self.assertIsNone(self.reader._topology)
        self.assertEqual(self.reader._request_after, {})


if __name__ == "__main__":
    unittest.main()
