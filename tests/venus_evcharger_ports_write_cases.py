# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from venus_evcharger.ports import WriteControllerPort


class TestWallboxPortsWrite(unittest.TestCase):
    @staticmethod
    def _service_source_text() -> str:
        root = Path(__file__).resolve().parents[1]
        source_files = [root / "venus_evcharger_service.py"]
        source_files.extend(
            path
            for path in sorted((root / "venus_evcharger").rglob("*.py"))
            if path.is_file()
        )
        return "\n".join(path.read_text(encoding="utf-8") for path in source_files)

    def test_base_ports_raise_for_unknown_attrs_and_missing_controller_bindings(self) -> None:
        service = SimpleNamespace(virtual_mode=0, virtual_autostart=1, virtual_startstop=0, virtual_enable=1, virtual_set_current=16.0, min_current=6.0, max_current=16.0, auto_start_condition_since=None, auto_stop_condition_since=None, manual_override_until=0.0, auto_manual_override_seconds=300.0, _auto_mode_cutover_pending=False, _ignore_min_offtime_once=False)
        write_port = WriteControllerPort(service)
        with self.assertRaises(AttributeError):
            _ = write_port.unknown_attr
        with self.assertRaises(AttributeError):
            write_port.unknown_attr = 1
        with self.assertRaises(AttributeError):
            write_port.clear_auto_samples()

    def test_port_allowed_methods_exist_on_service_class(self) -> None:
        source_text = "\n".join(Path(file_name).read_text(encoding="utf-8") for file_name in ("venus_evcharger_service.py", "venus_evcharger/service/auto.py", "venus_evcharger/service/runtime.py", "venus_evcharger/service/state_publish.py", "venus_evcharger/service/update.py"))
        from venus_evcharger.ports import UpdateCyclePort
        for method_name in sorted(UpdateCyclePort._ALLOWED_METHODS):
            with self.subTest(method_name=method_name):
                self.assertIn(f"def {method_name}(", source_text)

    def test_port_declared_attrs_are_referenced_in_service_code(self) -> None:
        source_text = self._service_source_text()
        from venus_evcharger.ports import AutoDecisionPort, DbusInputPort, UpdateCyclePort
        declared_attrs: set[str] = set()
        for port_class in (WriteControllerPort, DbusInputPort, UpdateCyclePort, AutoDecisionPort):
            declared_attrs.update(port_class._ALLOWED_ATTRS)
            declared_attrs.update(port_class._MUTABLE_ATTRS)
        for attr_name in sorted(declared_attrs):
            with self.subTest(attr_name=attr_name):
                self.assertIn(attr_name, source_text)

    def _service(self):
        return SimpleNamespace(
            virtual_mode=0, virtual_autostart=1, virtual_startstop=0, virtual_enable=1, virtual_set_current=16.0,
            min_current=6.0, max_current=16.0, auto_start_condition_since=None, auto_stop_condition_since=None,
            manual_override_until=0.0, auto_manual_override_seconds=300.0, _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False, _clear_auto_samples=MagicMock(), _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(), _get_worker_snapshot=MagicMock(return_value={"pm_status": None}),
            _update_worker_snapshot=MagicMock(), _publish_dbus_field=MagicMock(), _time_now=MagicMock(return_value=100.0),
            _normalize_mode=MagicMock(return_value=1), _mode_uses_auto_logic=MagicMock(return_value=True),
            _state_summary=MagicMock(return_value="state"), _save_runtime_state=MagicMock(),
        )

    def test_write_controller_port_forwards_state_and_methods(self) -> None:
        service = self._service()
        port = WriteControllerPort(service)
        port.virtual_mode = 2
        port.virtual_autostart = cast(Any, "0")
        port.virtual_enable = 5
        port.virtual_startstop = cast(Any, "bad")
        port.auto_mode_cutover_pending = cast(Any, 1)
        port.ignore_min_offtime_once = cast(Any, 0)
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertTrue(port.auto_mode_cutover_pending)
        self.assertFalse(port.ignore_min_offtime_once)
        port.queue_relay_command(True, 100.0)
        service._queue_relay_command.assert_called_once_with(True, 100.0)

    def test_write_controller_port_requires_confirmed_off_before_skipping_cutover(self) -> None:
        service = self._service()
        service._get_worker_snapshot.return_value = {"pm_status": {"output": False}, "pm_confirmed": False}
        service._peek_pending_relay_command = MagicMock(return_value=(False, 99.0))
        service._last_pm_status = {"output": False}
        service._last_pm_status_confirmed = False
        service._last_pm_status_at = 99.0
        port = WriteControllerPort(service)
        self.assertTrue(port.relay_may_be_on_for_cutover())
        service._get_worker_snapshot.return_value = {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 99.5}
        self.assertFalse(port.relay_may_be_on_for_cutover())

    def test_write_controller_port_covers_runtime_override_and_charger_error_paths(self) -> None:
        service = self._service()
        service.virtual_startstop = 1
        service.supported_phase_selections = ("P1",)
        service._get_worker_snapshot.return_value = {"pm_status": {"output": False}, "pm_confirmed": True}
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        service._last_pm_status = {"output": False}
        service._last_pm_status_confirmed = True
        service._last_pm_status_at = True
        port = WriteControllerPort(service)
        port.supported_phase_selections = ("P1_P2", "P1")
        port._software_update_run_requested_at = 42.5
        self.assertEqual(service.supported_phase_selections, ("P1_P2", "P1"))
        self.assertEqual(port._software_update_run_requested_at, 42.5)
        with patch.object(WriteControllerPort, "_relay_output_timestamp", return_value=None):
            self.assertIsNone(port._fresh_snapshot_output({"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 99.5}, 100.0, 2.0))
        self.assertIsNone(port._fresh_last_output(100.0, 2.0))
        with self.assertRaisesRegex(RuntimeError, "set_enabled configured"):
            port.charger_set_enabled(True)
        with self.assertRaisesRegex(RuntimeError, "set_current configured"):
            port.charger_set_current(12.0)

    def test_write_controller_port_staleness_paths(self) -> None:
        service = self._service()
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        service._last_pm_status = {"output": False}
        service._last_pm_status_confirmed = True
        service._last_pm_status_at = 95.0
        service._worker_poll_interval_seconds = 1.0
        service.relay_sync_timeout_seconds = 2.0
        service._get_worker_snapshot.return_value = {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 95.0}
        port = WriteControllerPort(service)
        self.assertTrue(port.relay_may_be_on_for_cutover())
        service._get_worker_snapshot.return_value = {"pm_status": None, "pm_confirmed": False}
        service._last_pm_status = None
        service._last_pm_status_confirmed = False
        service._last_pm_status_at = None
        self.assertTrue(port.relay_may_be_on_for_cutover())

    def test_write_controller_port_covers_fresh_last_output_and_validation_fallback(self) -> None:
        service = self._service()
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        service._get_worker_snapshot.return_value = {"pm_status": None, "pm_confirmed": False}
        service._last_confirmed_pm_status = None
        service._last_confirmed_pm_status_at = None
        service._last_pm_status = {"output": True}
        service._last_pm_status_confirmed = True
        service._last_pm_status_at = 99.5
        service._worker_poll_interval_seconds = 1.0
        service.relay_sync_timeout_seconds = 2.0
        service._validate_runtime_config = None
        port = WriteControllerPort(service)

        self.assertTrue(port._fresh_last_output(100.0, 2.0))
        self.assertTrue(port._fresh_confirmed_relay_output({"pm_status": None, "pm_confirmed": False}) is True)
        port.validate_runtime_config()
