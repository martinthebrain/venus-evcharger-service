# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct contracts for runtime and worker-state initialization."""

from __future__ import annotations

import threading
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from venus_evcharger.runtime.setup import RuntimeSetup
from venus_evcharger.runtime.setup_support import empty_worker_snapshot
from venus_evcharger.runtime.state_store import RuntimeStateStore


@dataclass(frozen=True)
class RuntimeSetupFixture:
    setup: RuntimeSetup
    health_code: MagicMock
    state_store: MagicMock
    async_state: MagicMock


def _setup(service: object) -> RuntimeSetupFixture:
    health_code = MagicMock(return_value=17)
    state_store = MagicMock()
    async_state = MagicMock()
    state_store.observability_defaults.return_value = {
        "_error_state": lambda: {"error": 3},
        "_failure_active": lambda: {"failure": True},
    }
    return RuntimeSetupFixture(
        setup=RuntimeSetup(service, health_code, state_store, async_state),
        health_code=health_code,
        state_store=state_store,
        async_state=async_state,
    )


def _assert_attributes(
    case: unittest.TestCase,
    service: object,
    expected: dict[str, object],
) -> None:
    for name, value in expected.items():
        case.assertEqual(getattr(service, name), value, name)


class RuntimeSetupContractTests(unittest.TestCase):
    def test_service_repo_root_accepts_class_or_instance_path_and_empty(self) -> None:
        class ClassPathService:
            _script_path_value = "/opt/evcharger/main.py"

        self.assertEqual(RuntimeSetup._service_repo_root(ClassPathService()), "/opt/evcharger")
        self.assertEqual(
            RuntimeSetup._service_repo_root(
                SimpleNamespace(_script_path_value="/data/evcharger/service.py")
            ),
            "/data/evcharger",
        )
        self.assertEqual(RuntimeSetup._service_repo_root(SimpleNamespace()), "")

    def test_system_uptime_parses_clamps_and_rejects_invalid_input(self) -> None:
        with patch("builtins.open", side_effect=OSError("missing")):
            self.assertIsNone(RuntimeSetup._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="invalid 2.0\n")):
            self.assertIsNone(RuntimeSetup._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="-2.5 2.0\n")):
            self.assertEqual(RuntimeSetup._system_uptime_seconds(), 0.0)
        opener = mock_open(read_data="12.5 3.0\n")
        with patch("builtins.open", opener):
            self.assertEqual(RuntimeSetup._system_uptime_seconds(), 12.5)
        opener.assert_called_once_with("/proc/uptime", "r", encoding="utf-8")

    def test_boot_delayed_update_due_contract(self) -> None:
        with patch.object(RuntimeSetup, "_system_uptime_seconds", return_value=None):
            self.assertIsNone(RuntimeSetup._boot_delayed_update_due_at(100.0, 10.0))
        with patch.object(RuntimeSetup, "_system_uptime_seconds", return_value=10.0):
            self.assertIsNone(RuntimeSetup._boot_delayed_update_due_at(100.0, 10.0))
        with patch.object(RuntimeSetup, "_system_uptime_seconds", return_value=3.0):
            self.assertEqual(RuntimeSetup._boot_delayed_update_due_at(100.0, 10.0), 107.0)
        with patch.object(RuntimeSetup, "_system_uptime_seconds", return_value=9.5):
            self.assertEqual(RuntimeSetup._boot_delayed_update_due_at(100.0, 10.0), 100.5)

    def test_read_local_version_uses_ordered_repository_candidates(self) -> None:
        with patch(
            "venus_evcharger.runtime.setup._first_existing_version_line",
            return_value="2.4.1",
        ) as read_version:
            self.assertEqual(RuntimeSetup._read_local_version("/repo"), "2.4.1")
        read_version.assert_called_once_with(
            ("/repo/.bootstrap-state/installed_version", "/repo/version.txt")
        )

    def test_initialize_runtime_support_owns_exact_initial_state(self) -> None:
        service = SimpleNamespace(core_command_mailbox_dir="/run/gateway/core")
        fixture = _setup(service)
        setup = fixture.setup
        inbox = object()
        session = object()
        metrics = {"state": "contract"}
        with (
            patch("venus_evcharger.runtime.setup.time.time", return_value=123.0),
            patch("venus_evcharger.runtime.setup.requests.Session", return_value=session),
            patch("venus_evcharger.runtime.setup.CoreCommandMailbox", return_value=inbox) as inbox_type,
            patch("venus_evcharger.runtime.setup.default_auto_metrics", return_value=metrics),
            patch("venus_evcharger.runtime.setup.initialize_victron_balance_runtime_state") as init_balance,
            patch("venus_evcharger.runtime.setup.initialize_runtime_override_state") as init_overrides,
            patch("venus_evcharger.runtime.setup.initialize_software_update_runtime_state") as init_update,
            patch.object(setup, "_service_repo_root", return_value="/repo") as repo_root,
            patch.object(setup, "_read_local_version", return_value="2.4.1") as local_version,
            patch.object(setup, "_boot_delayed_update_due_at", return_value=456.0) as boot_due,
        ):
            setup.initialize_runtime_support()

        inbox_type.assert_called_once_with("/run/gateway/core")
        repo_root.assert_called_once_with(service)
        local_version.assert_called_once_with("/repo")
        boot_due.assert_called_once_with(123.0, 3600.0)
        init_balance.assert_called_once_with(service)
        init_overrides.assert_called_once_with(service)
        init_update.assert_called_once_with(
            service,
            repo_root="/repo",
            started_at=123.0,
            current_version="2.4.1",
            boot_auto_due_at=456.0,
        )
        fixture.async_state.initialize.assert_called_once_with()
        fixture.health_code.assert_called_once_with("init")
        self.assertIs(service.session, session)
        self.assertIs(service._core_command_mailbox, inbox)
        self.assertIs(service._last_auto_metrics, metrics)
        _assert_attributes(
            self,
            service,
            {
                "last_update": 0,
                "_last_pv_missing_warning": None,
                "_last_battery_missing_warning": None,
                "_last_battery_allow_warning": None,
                "_last_grid_missing_warning": None,
                "_warning_state": {},
                "_error_state": {"error": 3},
                "_failure_active": {"failure": True},
                "_last_health_reason": "init",
                "_last_health_code": 17,
                "_last_auto_state": "idle",
                "_last_auto_state_code": 0,
                "_auto_cached_inputs_used": False,
                "_last_pv_value": None,
                "_last_pv_at": None,
                "_last_grid_value": None,
                "_last_grid_at": None,
                "_last_battery_soc_value": None,
                "_last_battery_soc_at": None,
                "_last_combined_battery_soc_value": None,
                "_last_combined_battery_soc_at": None,
                "_last_combined_battery_charge_power_w": None,
                "_last_combined_battery_charge_power_at": None,
                "_last_combined_battery_discharge_power_w": None,
                "_last_combined_battery_discharge_power_at": None,
                "_last_combined_battery_net_power_w": None,
                "_last_combined_battery_net_power_at": None,
                "_last_combined_battery_ac_power_w": None,
                "_last_combined_battery_ac_power_at": None,
                "_last_energy_cluster": {},
                "_last_energy_learning_profiles": {},
                "_last_pm_status": None,
                "_last_pm_status_at": None,
                "_last_pm_status_confirmed": False,
                "_last_shelly_warning": None,
                "_shelly_state": "unknown",
                "_shelly_last_error_reason": "",
                "_shelly_last_error_detail": "",
                "_shelly_last_error_at": None,
                "_shelly_consecutive_errors": 0,
                "_shelly_last_ok_at": None,
                "_shelly_retry_after": 0.0,
                "_shelly_session_reset_count": 0,
                "_shelly_offline_since": None,
                "_last_voltage": None,
                "_last_dbus_ok_at": None,
                "_last_successful_update_at": None,
                "_last_recovery_attempt_at": None,
                "_recovery_attempts": 0,
            },
        )

    def test_initialize_runtime_support_uses_configured_core_mailbox_directory(self) -> None:
        service = SimpleNamespace(core_command_mailbox_dir="/run/configured/core")
        setup = _setup(service).setup
        with (
            patch("venus_evcharger.runtime.setup.CoreCommandMailbox") as mailbox_type,
            patch("venus_evcharger.runtime.setup.initialize_victron_balance_runtime_state"),
            patch("venus_evcharger.runtime.setup.initialize_runtime_override_state"),
            patch("venus_evcharger.runtime.setup.initialize_software_update_runtime_state"),
            patch.object(setup, "_service_repo_root", return_value=""),
            patch.object(setup, "_read_local_version", return_value=""),
            patch.object(setup, "_boot_delayed_update_due_at", return_value=None),
        ):
            setup.initialize_runtime_support()
        mailbox_type.assert_called_once_with("/run/configured/core")

    def test_worker_snapshot_is_deep_copied_at_mutable_payload_boundaries(self) -> None:
        status = {"output": True}
        source = {"id": "battery"}
        profile = {"samples": 3}
        snapshot = {
            "pm_status": status,
            "battery_sources": [source, "sentinel"],
            "battery_learning_profiles": {"profile": profile},
        }
        cloned = RuntimeStateStore.clone_snapshot(snapshot)
        self.assertEqual(cloned, snapshot)
        self.assertIsNot(cloned, snapshot)
        self.assertIsNot(cloned["pm_status"], status)
        self.assertIsNot(cloned["battery_sources"], snapshot["battery_sources"])
        self.assertIsNot(cloned["battery_sources"][0], source)
        self.assertIsNot(cloned["battery_learning_profiles"], snapshot["battery_learning_profiles"])
        self.assertIsNot(cloned["battery_learning_profiles"]["profile"], profile)
        self.assertEqual(RuntimeStateStore.empty_snapshot(), empty_worker_snapshot())

    def test_init_worker_state_owns_exact_defaults(self) -> None:
        service = SimpleNamespace(poll_interval_ms=1000)
        setup = RuntimeStateStore(service)
        snapshot = {"snapshot": True}
        session = object()
        with (
            patch.object(setup, "empty_snapshot", return_value=snapshot) as empty,
            patch("venus_evcharger.runtime.state_store.requests.Session", return_value=session),
            patch("venus_evcharger.runtime.state_store.uuid.uuid4") as uuid4,
        ):
            uuid4.return_value.hex = "runtime-id"
            setup.initialize_worker_state()
        empty.assert_called_once_with()
        uuid4.assert_called_once_with()
        self.assertIs(service._worker_snapshot, snapshot)
        self.assertIs(service._worker_session, session)
        self.assertIsInstance(service._worker_stop_event, threading.Event)
        self.assertTrue(hasattr(service._worker_snapshot_lock, "acquire"))
        self.assertTrue(hasattr(service._relay_command_lock, "acquire"))
        _assert_attributes(
            self,
            service,
            {
                "_worker_poll_interval_seconds": 1.0,
                "_worker_thread": None,
                "_pending_relay_state": None,
                "_pending_relay_requested_at": None,
                "relay_sync_timeout_seconds": 3.0,
                "_relay_sync_expected_state": None,
                "_relay_sync_requested_at": None,
                "_relay_sync_deadline_at": None,
                "_relay_sync_failure_reported": False,
                "_auto_input_helper_process": None,
                "_auto_input_helper_generation": 0,
                "_auto_input_runtime_instance_id": "runtime-id",
                "_auto_input_helper_last_start_at": 0.0,
                "_auto_input_helper_restart_requested_at": None,
                "_auto_input_snapshot_last_seen": None,
                "_auto_input_snapshot_seen_for_current_helper": False,
                "_auto_input_snapshot_mtime_ns": None,
                "_auto_input_snapshot_last_captured_at": None,
                "_auto_input_snapshot_version": None,
                "_auto_input_snapshot_writer_pid": None,
                "_auto_input_snapshot_generation": None,
                "_auto_input_snapshot_runtime_instance_id": None,
            },
        )

        short_poll_service = SimpleNamespace(poll_interval_ms=500)
        short_poll_setup = RuntimeStateStore(short_poll_service)
        with patch("venus_evcharger.runtime.state_store.uuid.uuid4") as short_uuid:
            short_uuid.return_value.hex = "short-id"
            short_poll_setup.initialize_worker_state()
        self.assertEqual(short_poll_service._worker_poll_interval_seconds, 0.5)
        self.assertEqual(short_poll_service.relay_sync_timeout_seconds, 2.0)


if __name__ == "__main__":
    unittest.main()
