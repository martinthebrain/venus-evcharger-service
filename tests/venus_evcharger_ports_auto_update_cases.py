# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.ports import AutoDecisionPort


class TestWallboxPortsAutoUpdate(unittest.TestCase):
    def test_auto_decision_port_does_not_bind_or_proxy_controller_behavior(self) -> None:
        port = AutoDecisionPort(SimpleNamespace())

        self.assertFalse(hasattr(port, "bind_controller"))
        with self.assertRaises(AttributeError):
            getattr(port, "clear_auto_samples")()

    def test_auto_decision_port_forwards_audit_and_pending_helpers(self) -> None:
        service = SimpleNamespace(
            auto_policy=AutoPolicy(),
            auto_samples=[],
            auto_average_window_seconds=60.0,
            relay_last_changed_at=None,
            relay_last_off_at=None,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            _last_health_reason="init",
            _last_health_code=0,
            auto_min_runtime_seconds=0.0,
            auto_min_offtime_seconds=0.0,
            _last_grid_at=None,
            auto_grid_missing_stop_seconds=60.0,
            virtual_mode=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _last_battery_allow_warning=None,
            auto_allow_without_battery_soc=False,
            auto_battery_scan_interval_seconds=60.0,
            auto_stop_delay_seconds=30.0,
            auto_night_lock_stop=False,
            _last_auto_metrics={},
            started_at=0.0,
            auto_startup_warmup_seconds=0.0,
            manual_override_until=0.0,
            virtual_autostart=1,
            auto_start_delay_seconds=30.0,
            _auto_cached_inputs_used=False,
            virtual_enable=1,
            virtual_startstop=0,
            auto_daytime_only=False,
            auto_month_windows={},
            auto_audit_log=True,
            state=SimpleNamespace(save_runtime_state=MagicMock(return_value="saved")),
            runtime=SimpleNamespace(
                write_auto_audit_event=MagicMock(return_value="audited"),
                pending_relay_command=MagicMock(return_value=(True, 123.0)),
            ),
        )
        port = AutoDecisionPort(service)
        self.assertEqual(port.save_runtime_state(), "saved")
        self.assertEqual(port.write_auto_audit_event("running", False), "audited")
        self.assertEqual(port.peek_pending_relay_command(), (True, 123.0))

    def test_auto_decision_port_validates_pending_relay_command_contract(self) -> None:
        runtime = SimpleNamespace(pending_relay_command=MagicMock(return_value=[True, 1.0]))
        service = SimpleNamespace(runtime=runtime)
        port = AutoDecisionPort(service)
        with self.assertRaisesRegex(TypeError, "peek_pending_relay_command must return tuple"):
            port.peek_pending_relay_command()
        runtime.pending_relay_command.return_value = (True,)
        with self.assertRaisesRegex(TypeError, "tuple length 2"):
            port.peek_pending_relay_command()
        runtime.pending_relay_command.return_value = (1, 1.0)
        with self.assertRaisesRegex(TypeError, "state must be bool\\|None"):
            port.peek_pending_relay_command()
        runtime.pending_relay_command.return_value = (True, True)
        with self.assertRaisesRegex(TypeError, "timestamp must be int\\|float\\|None"):
            port.peek_pending_relay_command()
        runtime.pending_relay_command.return_value = (False, 2)
        self.assertEqual(port.peek_pending_relay_command(), (False, 2.0))

    def test_auto_decision_port_does_not_forward_runtime_fields(self) -> None:
        service = SimpleNamespace(virtual_mode=2, _last_confirmed_pm_status={"output": True})
        port = AutoDecisionPort(service)

        self.assertIs(port.service, service)
        with self.assertRaises(AttributeError):
            getattr(port, "virtual_mode")
        with self.assertRaises(AttributeError):
            getattr(port, "_last_confirmed_pm_status")
