# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway write and retry contracts for Victron ESS balancing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.update import victron_ess_balance_apply_write as write_module
from venus_evcharger.update.victron_ess_balance_apply_write import (
    VictronEssSetpointWriter,
)
from venus_evcharger.update.victron_ess_balance_apply_sources import VictronEssSourceResolver


class VictronEssBalanceApplyWriteContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = VictronEssSetpointWriter(VictronEssSourceResolver())

    def test_should_write_enforces_interval_and_one_watt_delta_boundaries(self) -> None:
        svc = SimpleNamespace(auto_battery_discharge_balance_victron_bias_min_update_seconds=3.0)
        self.assertIs(self.writer._victron_ess_balance_should_write(svc, 10.0, 50.0), True)
        svc._victron_ess_balance_last_write_at = 8.0
        svc._victron_ess_balance_last_setpoint_w = 40.0
        self.assertIs(self.writer._victron_ess_balance_should_write(svc, 10.0, 50.0), False)
        self.assertIs(self.writer._victron_ess_balance_should_write(svc, 11.0, 40.99), False)
        self.assertIs(self.writer._victron_ess_balance_should_write(svc, 11.0, 41.0), True)
        self.assertIs(self.writer._victron_ess_balance_should_write(svc, 11.0, 39.0), True)
        svc.auto_battery_discharge_balance_victron_bias_min_update_seconds = -2.0
        self.assertIs(self.writer._victron_ess_balance_should_write(svc, 8.0, 41.0), True)
        no_interval = SimpleNamespace(
            _victron_ess_balance_last_write_at=10.0,
            _victron_ess_balance_last_setpoint_w=0.0,
        )
        self.assertIs(self.writer._victron_ess_balance_should_write(no_interval, 10.0, 2.0), True)

    def test_last_setpoint_and_target_normalization_are_exact(self) -> None:
        self.assertIsNone(self.writer._victron_ess_balance_last_setpoint(SimpleNamespace()))
        self.assertEqual(
            self.writer._victron_ess_balance_last_setpoint(
                SimpleNamespace(_victron_ess_balance_last_setpoint_w=12.5)
            ),
            12.5,
        )
        self.assertEqual(self.writer._victron_ess_balance_write_target(" service ", " /Path "), ("service", "/Path"))
        self.assertEqual(self.writer._victron_ess_balance_write_target(None, None), ("", ""))
        self.assertEqual(self.writer._victron_ess_balance_write_payload(object(), 7), 7.0)

    def test_try_write_enqueues_exact_gateway_command(self) -> None:
        svc = SimpleNamespace(dbus_gateway_run_dir=" /run/gateway ")
        client = MagicMock()
        with (
            patch.object(write_module, "gateway_paths", return_value="paths") as paths,
            patch.object(write_module, "GatewayClient", return_value=client) as client_type,
            patch.object(self.writer, "_victron_ess_balance_write_payload", return_value=12.5) as payload,
        ):
            self.assertIsNone(self.writer._victron_ess_balance_try_write_setpoint(svc, "service", "/Path", 12.5))
        paths.assert_called_once_with(" /run/gateway ")
        client_type.assert_called_once_with("paths")
        payload.assert_called_once_with(None, 12.5)
        client.enqueue_command.assert_called_once_with(
            {
                "kind": "set_value",
                "source": "victron-ess-balance",
                "service": "service",
                "path": "/Path",
                "value": 12.5,
                "priority": "user",
                "coalesce_key": "service:/Path",
            }
        )

    def test_try_write_uses_default_gateway_paths_when_run_dir_is_absent(self) -> None:
        client = MagicMock()
        with (
            patch.object(write_module, "gateway_paths", return_value="paths") as paths,
            patch.object(write_module, "GatewayClient", return_value=client),
        ):
            self.writer._victron_ess_balance_try_write_setpoint(SimpleNamespace(), "service", "/Path", 1.0)
        paths.assert_called_once_with(None)

    def test_retry_log_and_write_error_lifecycle_are_exact(self) -> None:
        error = RuntimeError("offline")
        with patch.object(write_module.logging, "debug") as debug:
            self.writer._victron_ess_balance_log_write_retry("service", "/Path", error)
        debug.assert_called_once_with(
            "Victron ESS balance-bias write retry for %s %s after error: %s",
            "service",
            "/Path",
            error,
        )

        svc = object()
        with patch.object(self.writer, "_victron_ess_balance_try_write_setpoint") as attempt:
            self.assertIsNone(self.writer._victron_ess_balance_write_error(svc, "service", "/Path", 2.0))
        attempt.assert_called_once_with(svc, "service", "/Path", 2.0)

        second_error = ValueError("again")
        with (
            patch.object(self.writer, "_victron_ess_balance_try_write_setpoint", side_effect=[error, second_error]) as attempt,
            patch.object(self.writer, "_victron_ess_balance_log_write_retry") as retry,
        ):
            self.assertIs(self.writer._victron_ess_balance_write_error(svc, "service", "/Path", 2.0), second_error)
        self.assertEqual(attempt.call_args_list, [call(svc, "service", "/Path", 2.0), call(svc, "service", "/Path", 2.0)])
        retry.assert_called_once_with("service", "/Path", error)

        with patch.object(
            self.writer,
            "_victron_ess_balance_try_write_setpoint",
            side_effect=(error, None),
        ):
            self.assertIsNone(self.writer._victron_ess_balance_write_error(svc, "service", "/Path", 2.0))

    def test_write_setpoint_rejects_invalid_target_and_reports_failures(self) -> None:
        warning = MagicMock()
        svc = SimpleNamespace(runtime=SimpleNamespace(warning_throttled=warning))
        with patch.object(self.writer, "_victron_ess_balance_write_error") as write_error:
            self.assertIs(self.writer._victron_ess_balance_write_setpoint(svc, "", "/Path", 1.0), False)
            self.assertIs(self.writer._victron_ess_balance_write_setpoint(svc, "service", "", 1.0), False)
        write_error.assert_not_called()

        with patch.object(self.writer, "_victron_ess_balance_write_error", return_value=None) as write_error:
            self.assertIs(self.writer._victron_ess_balance_write_setpoint(svc, " service ", " /Path ", 3.0), True)
        write_error.assert_called_once_with(svc, "service", "/Path", 3.0)
        warning.assert_not_called()

        error = OSError("failed")
        with (
            patch.object(self.writer, "_victron_ess_balance_write_error", return_value=error),
            patch.object(self.writer, "_victron_ess_balance_write_warning_interval_seconds", return_value=8.0) as interval,
        ):
            self.assertIs(self.writer._victron_ess_balance_write_setpoint(svc, "service", "/Path", 4.0), False)
        interval.assert_called_once_with(svc)
        warning.assert_called_once_with(
            "victron-ess-balance-write-failed",
            8.0,
            "Victron ESS balance-bias write to %s %s failed: %s",
            "service",
            "/Path",
            error,
        )

    def test_warning_interval_and_direct_dbus_boundary(self) -> None:
        self.assertEqual(self.writer._victron_ess_balance_write_warning_interval_seconds(SimpleNamespace()), 5.0)
        self.assertEqual(
            self.writer._victron_ess_balance_write_warning_interval_seconds(
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_min_update_seconds=3.0)
            ),
            5.0,
        )
        self.assertEqual(
            self.writer._victron_ess_balance_write_warning_interval_seconds(
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_min_update_seconds=9.0)
            ),
            9.0,
        )
        with self.assertRaises(RuntimeError) as raised:
            self.writer._victron_ess_balance_dbus_module()
        self.assertEqual(str(raised.exception), "Direct DBus access is disabled; use the DBus gateway adapter")


if __name__ == "__main__":
    unittest.main()
