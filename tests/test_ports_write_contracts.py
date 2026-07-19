# SPDX-License-Identifier: GPL-3.0-or-later
"""Safety and delegation contracts for the write-controller port."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.ports.write import WriteControllerPort


def _service_double(**overrides: object) -> SimpleNamespace:
    auto = SimpleNamespace(
        clear_samples=MagicMock(return_value="cleared"),
        normalize_mode=MagicMock(return_value=2),
        mode_uses_auto_logic=MagicMock(return_value=1),
    )
    runtime = SimpleNamespace(
        queue_relay_command=MagicMock(return_value="queued"),
        publish_local_pm_status=MagicMock(return_value="published"),
        worker_snapshot=MagicMock(return_value={}),
        pending_relay_command=MagicMock(return_value=(False, 99.0)),
        update_worker_snapshot=MagicMock(return_value="updated"),
        phase_selection_requires_pause=MagicMock(return_value=1),
        apply_phase_selection=MagicMock(return_value="P1_P2_P3"),
    )
    state = SimpleNamespace(
        publish_field=MagicMock(return_value="dbus-published"),
        summary=MagicMock(return_value="ready"),
        save_runtime_state=MagicMock(return_value="saved"),
        save_runtime_overrides=MagicMock(),
        validate_runtime_config=MagicMock(),
    )
    values: dict[str, object] = {
        "supported_phase_selections": ("P1_P2_P3", "P1"),
        "auto": auto,
        "runtime": runtime,
        "state": state,
        "time_now": MagicMock(return_value=100.25),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class WriteControllerPortContractTests(unittest.TestCase):
    def test_direct_service_operations_forward_exact_arguments_and_results(self) -> None:
        service = _service_double()
        port = WriteControllerPort(service)

        self.assertEqual(port.clear_auto_samples(), "cleared")
        self.assertEqual(port.queue_relay_command(True, 12.5), "queued")
        self.assertEqual(port.publish_local_pm_status(False, 13.5), "published")
        self.assertEqual(port.get_worker_snapshot(), {})
        self.assertEqual(port.update_worker_snapshot(pm_status={"output": True}), "updated")
        self.assertEqual(port.publish_dbus_field("mode", 2, 14.5, force=True), "dbus-published")
        self.assertEqual(port.publish_dbus_field("status", 1, 15.5), "dbus-published")
        self.assertEqual(port.time_now(), 100.25)

        service.auto.clear_samples.assert_called_once_with()
        service.runtime.queue_relay_command.assert_called_once_with(True, 12.5)
        service.runtime.publish_local_pm_status.assert_called_once_with(False, 13.5)
        service.runtime.worker_snapshot.assert_called_once_with()
        service.runtime.update_worker_snapshot.assert_called_once_with(pm_status={"output": True})
        self.assertEqual(
            service.state.publish_field.call_args_list,
            [call("mode", 2, 14.5, force=True), call("status", 1, 15.5, force=False)],
        )
        service.time_now.assert_called_once_with()

    def test_relay_freshness_budget_uses_the_tightest_positive_runtime_limit(self) -> None:
        self.assertEqual(WriteControllerPort(_service_double())._relay_status_freshness_seconds(), 2.0)
        self.assertEqual(
            WriteControllerPort(_service_double(_worker_poll_interval_seconds=0.75))._relay_status_freshness_seconds(),
            1.5,
        )
        self.assertEqual(
            WriteControllerPort(_service_double(relay_sync_timeout_seconds=1.25))._relay_status_freshness_seconds(),
            1.25,
        )
        service = _service_double(_worker_poll_interval_seconds=0.75, relay_sync_timeout_seconds=1.25)
        self.assertEqual(WriteControllerPort(service)._relay_status_freshness_seconds(), 1.25)
        service = _service_double(_worker_poll_interval_seconds=0, relay_sync_timeout_seconds=-1)
        self.assertEqual(WriteControllerPort(service)._relay_status_freshness_seconds(), 2.0)
        service = _service_double(relay_sync_timeout_seconds=0)
        self.assertEqual(WriteControllerPort(service)._relay_status_freshness_seconds(), 2.0)
        service = _service_double(relay_sync_timeout_seconds=0.75)
        self.assertEqual(WriteControllerPort(service)._relay_status_freshness_seconds(), 1.0)
        service = _service_double(_worker_poll_interval_seconds="0.25", relay_sync_timeout_seconds="0.75")
        self.assertEqual(WriteControllerPort(service)._relay_status_freshness_seconds(), 1.0)

    def test_snapshot_output_requires_confirmation_shape_timestamp_and_freshness(self) -> None:
        fresh = {"pm_status": {"output": True}, "pm_confirmed": True, "pm_captured_at": 98.0}
        self.assertIs(WriteControllerPort._fresh_snapshot_output(fresh, 100.0, 2.0), True)
        fallback_time = {"pm_status": {"output": 0}, "pm_confirmed": True, "captured_at": 101.0}
        self.assertIs(WriteControllerPort._fresh_snapshot_output(fallback_time, 100.0, 2.0), False)

        invalid_snapshots = (
            {"pm_status": {"output": True}, "pm_confirmed": False, "pm_captured_at": 99.0},
            {"pm_status": "on", "pm_confirmed": True, "pm_captured_at": 99.0},
            {"pm_status": {}, "pm_confirmed": True, "pm_captured_at": 99.0},
            {"pm_status": {"output": True}, "pm_confirmed": True, "pm_captured_at": 97.999},
            {"pm_status": {"output": True}, "pm_confirmed": True, "pm_captured_at": 101.001},
        )
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot=snapshot):
                self.assertIsNone(WriteControllerPort._fresh_snapshot_output(snapshot, 100.0, 2.0))

        raw_snapshot = object()
        normalized = {
            "pm_status": {"output": True},
            "pm_confirmed": True,
            "pm_captured_at": 99.0,
        }
        with patch("venus_evcharger.ports.write.normalized_worker_snapshot", return_value=normalized) as normalize:
            self.assertIs(WriteControllerPort._fresh_snapshot_output(raw_snapshot, 100.0, 2.0), True)
        normalize.assert_called_once_with(raw_snapshot, now=100.0, clamp_future_timestamps=False)

        normalized_without_confirmation = dict(normalized)
        normalized_without_confirmation["pm_confirmed"] = False
        with patch(
            "venus_evcharger.ports.write.normalized_worker_snapshot",
            return_value=normalized_without_confirmation,
        ):
            self.assertIsNone(WriteControllerPort._fresh_snapshot_output(raw_snapshot, 100.0, 2.0))

    def test_relay_payload_helpers_define_exact_boundary_semantics(self) -> None:
        present = WriteControllerPort._relay_output_payload_present
        self.assertTrue(present(True, {"output": False}, 0.0))
        self.assertFalse(present(False, {"output": False}, 0.0))
        self.assertFalse(present(True, [], 0.0))
        self.assertFalse(present(True, {}, 0.0))
        self.assertFalse(present(True, {"output": False}, None))

        timestamp = WriteControllerPort._relay_output_timestamp
        self.assertEqual(timestamp(0), 0.0)
        self.assertEqual(timestamp(1.5), 1.5)
        self.assertIsNone(timestamp(True))
        self.assertIsNone(timestamp("1.5"))
        self.assertIsNone(timestamp(None))

        fresh = WriteControllerPort._relay_output_timestamp_fresh
        self.assertTrue(fresh(100.0, 98.0, 2.0))
        self.assertTrue(fresh(100.0, 101.0, 2.0))
        self.assertFalse(fresh(100.0, 97.999, 2.0))
        self.assertFalse(fresh(100.0, 101.001, 2.0))

        value = WriteControllerPort._relay_output_value
        self.assertIs(value({"output": 0}), False)
        self.assertIs(value({"output": "off"}), True)
        self.assertIsNone(value({}))
        self.assertIsNone(value("on"))

        pending = WriteControllerPort._pending_relay_state_on
        self.assertFalse(pending([]))
        self.assertFalse(pending(()))
        self.assertFalse(pending((0, 1.0)))
        self.assertTrue(pending((1,)))

    def test_last_relay_sample_uses_only_canonical_confirmed_snapshot(self) -> None:
        primary = _service_double(
            _last_confirmed_pm_status={},
            _last_confirmed_pm_status_at=12.0,
            _last_pm_status={"output": True},
            _last_pm_status_confirmed=True,
            _last_pm_status_at=13.0,
        )
        self.assertEqual(WriteControllerPort(primary)._last_relay_output_sample(), ({}, 12.0))

        obsolete_fields_only = _service_double(
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=12.0,
            _last_pm_status={"output": False},
            _last_pm_status_confirmed=True,
            _last_pm_status_at=13.0,
        )
        self.assertEqual(
            WriteControllerPort(obsolete_fields_only)._last_relay_output_sample(),
            (None, 12.0),
        )

        missing_flag = _service_double(_last_pm_status={"output": True}, _last_pm_status_at=14.0)
        self.assertEqual(WriteControllerPort(missing_flag)._last_relay_output_sample(), (None, None))
        missing_fallback = _service_double(_last_pm_status_confirmed=True)
        self.assertEqual(WriteControllerPort(missing_fallback)._last_relay_output_sample(), (None, None))

    def test_cutover_is_conservative_without_fresh_confirmation(self) -> None:
        service = _service_double()
        service.runtime.worker_snapshot.return_value = {}
        service.runtime.pending_relay_command.return_value = (True, 99.0)
        port = WriteControllerPort(service)
        with patch.object(WriteControllerPort, "_fresh_confirmed_relay_output") as confirmed:
            self.assertTrue(port.relay_may_be_on_for_cutover())
            confirmed.assert_not_called()

        service.runtime.pending_relay_command.return_value = (False, 99.0)
        with patch.object(WriteControllerPort, "_fresh_confirmed_relay_output", return_value=False) as confirmed:
            self.assertFalse(port.relay_may_be_on_for_cutover())
            confirmed.assert_called_once_with({})
        with patch.object(WriteControllerPort, "_fresh_confirmed_relay_output", return_value=True):
            self.assertTrue(port.relay_may_be_on_for_cutover())
        with patch.object(WriteControllerPort, "_fresh_confirmed_relay_output", return_value=None):
            self.assertTrue(port.relay_may_be_on_for_cutover())

    def test_charger_backend_capabilities_and_commands_are_explicit(self) -> None:
        service = _service_double()
        port = WriteControllerPort(service)
        self.assertFalse(port.charger_enable_available())
        self.assertFalse(port.charger_current_available())

        backend = SimpleNamespace(set_enabled=MagicMock(return_value="enabled"))
        service._charger_backend = backend
        self.assertTrue(port.charger_enable_available())
        self.assertFalse(port.charger_current_available())
        self.assertEqual(port.charger_set_enabled(1), "enabled")
        backend.set_enabled.assert_called_once_with(True)
        with self.assertRaisesRegex(RuntimeError, "^No charger backend with set_current configured$"):
            port.charger_set_current(6.0)

        service._charger_backend = SimpleNamespace(set_enabled="not-callable")
        self.assertFalse(port.charger_enable_available())
        with self.assertRaisesRegex(RuntimeError, "^No charger backend with set_enabled configured$"):
            port.charger_set_enabled(True)

        service._charger_backend = backend
        backend.set_current = MagicMock(return_value="current")
        self.assertTrue(port.charger_current_available())
        self.assertEqual(port.charger_set_current("7.5"), "current")
        backend.set_current.assert_called_once_with(7.5)

        service._charger_backend = None
        with self.assertRaisesRegex(RuntimeError, "^No charger backend with set_enabled configured$"):
            port.charger_set_enabled(True)

    def test_phase_mode_and_state_contracts_are_normalized_and_forwarded(self) -> None:
        service = _service_double()
        port = WriteControllerPort(service)

        self.assertTrue(port.phase_selection_requires_pause())
        self.assertEqual(port.apply_phase_selection("P1"), "P1_P2_P3")
        self.assertEqual(port.normalize_phase_selection("invalid"), "P1_P2_P3")
        self.assertEqual(port.normalize_phase_selection("invalid", "P1"), "P1")
        self.assertEqual(port.normalize_phase_selection("P1"), "P1")
        service.supported_phase_selections = ("invalid",)
        self.assertEqual(port.normalize_phase_selection("invalid"), "P1")
        self.assertEqual(port.normalize_mode("scheduled"), 2)
        self.assertTrue(port.mode_uses_auto_logic(2))
        self.assertEqual(port.state_summary(), "ready")
        self.assertEqual(port.save_runtime_state(), "saved")

        service.runtime.phase_selection_requires_pause.assert_called_once_with()
        service.runtime.apply_phase_selection.assert_called_once_with("P1")
        service.auto.normalize_mode.assert_called_once_with("scheduled")
        service.auto.mode_uses_auto_logic.assert_called_once_with(2)
        service.state.summary.assert_called_once_with()
        service.state.save_runtime_state.assert_called_once_with()

        service.runtime.apply_phase_selection.return_value = 3
        with self.assertRaisesRegex(TypeError, "apply_phase_selection must return str, got int"):
            port.apply_phase_selection("P1")
        service.state.summary.return_value = None
        with self.assertRaisesRegex(TypeError, "state.summary must return str, got NoneType"):
            port.state_summary()

    def test_state_runtime_hooks_are_explicit_role_contracts(self) -> None:
        service = _service_double()
        port = WriteControllerPort(service)
        port.save_runtime_overrides()
        port.validate_runtime_config()
        service.state.save_runtime_overrides.assert_called_once_with()
        service.state.validate_runtime_config.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
