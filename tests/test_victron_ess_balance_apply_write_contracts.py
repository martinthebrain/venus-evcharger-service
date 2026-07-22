# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic gateway write contracts for Victron ESS balancing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.ports.gateway_operations import (
    EssSetpointIntent,
    GatewayOperationReceipt,
    GxRelaySetRequest,
)
from venus_evcharger.update import victron_ess_balance_apply_write as write_module
from venus_evcharger.update.victron_ess_balance_apply_sources import VictronEssSourceResolver
from venus_evcharger.update.victron_ess_balance_apply_write import VictronEssSetpointWriter


class _GatewayOperations:
    def __init__(self) -> None:
        self.accepted = True
        self.error: BaseException | None = None
        self.ess_calls: list[tuple[float, EssSetpointIntent]] = []

    def read_gx_relay_state(self, relay_index: int, *, max_age_seconds: float) -> int | None:
        del relay_index, max_age_seconds
        return None

    def set_gx_relay_enabled(
        self,
        request: GxRelaySetRequest,
    ) -> GatewayOperationReceipt:
        del request
        return GatewayOperationReceipt(accepted=True)

    def set_ess_grid_setpoint(
        self,
        watts: float,
        *,
        intent: EssSetpointIntent,
    ) -> GatewayOperationReceipt:
        self.ess_calls.append((float(watts), intent))
        if self.error is not None:
            raise self.error
        return GatewayOperationReceipt(accepted=self.accepted, command_id="ess" if self.accepted else "")


def _service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "runtime": SimpleNamespace(warning_throttled=MagicMock()),
        "auto_battery_discharge_balance_victron_bias_min_update_seconds": 3.0,
        "_victron_ess_balance_last_write_at": None,
        "_victron_ess_balance_last_setpoint_w": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class VictronEssBalanceApplyWriteContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = _GatewayOperations()
        self.writer = VictronEssSetpointWriter(VictronEssSourceResolver(), self.gateway)

    def test_should_write_enforces_interval_and_one_watt_delta_boundaries(self) -> None:
        svc = _service()
        self.assertTrue(self.writer.should_write(svc, 10.0, 50.0))
        svc._victron_ess_balance_last_write_at = 8.0
        svc._victron_ess_balance_last_setpoint_w = 40.0
        self.assertFalse(self.writer.should_write(svc, 10.0, 50.0))
        self.assertFalse(self.writer.should_write(svc, 11.0, 40.99))
        self.assertTrue(self.writer.should_write(svc, 11.0, 41.0))
        self.assertTrue(self.writer.should_write(svc, 11.0, 39.0))
        svc.auto_battery_discharge_balance_victron_bias_min_update_seconds = -2.0
        self.assertTrue(self.writer.should_write(svc, 8.0, 41.0))

    def test_last_setpoint_normalizes_runtime_values(self) -> None:
        self.assertIsNone(self.writer.last_setpoint(_service()))
        self.assertEqual(self.writer.last_setpoint(_service(_victron_ess_balance_last_setpoint_w=12.5)), 12.5)
        self.assertIsNone(self.writer.last_setpoint(_service(_victron_ess_balance_last_setpoint_w="invalid")))

    def test_tracking_and_restore_are_forwarded_as_semantic_operations(self) -> None:
        svc = _service()
        self.assertTrue(self.writer.write_setpoint(svc, 12.5, intent="tracking"))
        self.assertTrue(self.writer.write_setpoint(svc, 0.0, intent="restore"))
        self.assertEqual(self.gateway.ess_calls, [(12.5, "tracking"), (0.0, "restore")])
        svc.runtime.warning_throttled.assert_not_called()

    def test_rejected_operation_is_reported_with_throttled_warning(self) -> None:
        svc = _service(auto_battery_discharge_balance_victron_bias_min_update_seconds=8.0)
        self.gateway.accepted = False
        self.assertFalse(self.writer.write_setpoint(svc, 4.0, intent="tracking"))
        svc.runtime.warning_throttled.assert_called_once_with(
            "victron-ess-balance-write-failed",
            8.0,
            "Victron ESS balance-bias %s operation was rejected: %s",
            "tracking",
            unittest.mock.ANY,
        )

    def test_gateway_errors_are_contained_and_logged(self) -> None:
        svc = _service()
        self.gateway.error = OSError("offline")
        with patch.object(write_module.logging, "debug") as debug:
            self.assertFalse(self.writer.write_setpoint(svc, 4.0, intent="restore"))
        debug.assert_called_once()
        svc.runtime.warning_throttled.assert_called_once()

    def test_warning_interval_has_five_second_floor(self) -> None:
        self.assertEqual(self.writer.warning_interval_seconds(_service()), 5.0)
        self.assertEqual(
            self.writer.warning_interval_seconds(
                _service(auto_battery_discharge_balance_victron_bias_min_update_seconds=9.0)
            ),
            9.0,
        )


if __name__ == "__main__":
    unittest.main()
