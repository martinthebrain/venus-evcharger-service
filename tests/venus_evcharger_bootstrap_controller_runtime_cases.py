# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.bootstrap.runtime import RuntimeInitializer


class _StatePort:
    def __init__(self) -> None:
        self.load_runtime_state = MagicMock()
        self.start_companion_bridge = MagicMock()

    def summary(self) -> str:
        return "mode=1"


class _RuntimePort:
    def __init__(self) -> None:
        self.mark_mainloop_thread = MagicMock()
        self.start_io_worker = MagicMock()
        self.start_update_worker = MagicMock()
        self.start_control_command_worker = MagicMock()
        self.start_mainloop_watchdog = MagicMock()
        self.schedule_update_cycle = MagicMock(return_value=True)
        self.flush_dbus_publish_queue = MagicMock(return_value=True)
        self.mainloop_heartbeat_tick = MagicMock(return_value=True)


class _UpdatePort:
    def update(self) -> bool:
        return True

    def sign_of_life(self) -> bool:
        return True


class _ControlPort:
    def __init__(self) -> None:
        self.start_server = MagicMock()


def _runtime_service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "runtime": _RuntimePort(),
        "state": _StatePort(),
        "update": _UpdatePort(),
        "control": _ControlPort(),
        "poll_interval_ms": 1000,
        "sign_of_life_minutes": 10,
        "runtime_state_path": "/run/state.json",
        "topology_configured": True,
        "_dbus_publish_flush_interval_ms": 200,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _initializer(service: object, *, mode_uses_auto: bool = False) -> RuntimeInitializer:
    return RuntimeInitializer(
        service,
        normalize_mode=lambda value: int(value),
        mode_uses_auto_logic=lambda _mode: mode_uses_auto,
        read_version=lambda _name: "1.0",
        gobject=MagicMock(),
    )


class TestServiceBootstrapRuntimeComponent(unittest.TestCase):
    def test_initialize_controllers_uses_explicit_controller_owner_port(self) -> None:
        owner = SimpleNamespace(
            prepare_runtime_state=MagicMock(),
            initialize_runtime=MagicMock(return_value=object()),
        )
        service = _runtime_service(controllers=owner)

        _initializer(service).initialize_controllers()

        owner.initialize_runtime.assert_called_once_with()

    def test_prepare_runtime_state_uses_explicit_controller_owner_port(self) -> None:
        owner = SimpleNamespace(
            prepare_runtime_state=MagicMock(),
            initialize_runtime=MagicMock(return_value=object()),
        )
        service = _runtime_service(controllers=owner)

        _initializer(service).prepare_runtime_state()

        owner.prepare_runtime_state.assert_called_once_with()

    def test_initialize_virtual_state_applies_configured_values(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"Mode": "1", "SetCurrent": "12.5", "PhaseSelection": "P1"}})
        service = _runtime_service(
            config=parser,
            max_current=16.0,
            _switch_backend=SimpleNamespace(
                capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1",)))
            ),
        )

        _initializer(service).initialize_virtual_state()

        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service.supported_phase_selections, ("P1",))
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_restore_runtime_state_records_manual_target_without_reinitializing_workers(self) -> None:
        service = _runtime_service(virtual_mode=0, virtual_enable=0, virtual_startstop=1)

        _initializer(service).restore_runtime_state()

        self.assertTrue(service._startup_manual_target)
        service.state.load_runtime_state.assert_called_once_with()

    def test_restore_runtime_state_does_not_carry_manual_target_into_auto(self) -> None:
        service = _runtime_service(virtual_mode=1, virtual_enable=1, virtual_startstop=1)

        _initializer(service, mode_uses_auto=True).restore_runtime_state()

        self.assertIsNone(service._startup_manual_target)

    def test_apply_device_metadata_delegates_with_owned_dependencies(self) -> None:
        service = _runtime_service()
        initializer = _initializer(service)
        with patch("venus_evcharger.bootstrap.runtime.apply_device_metadata") as apply:
            initializer.apply_device_metadata()

        self.assertIs(apply.call_args.args[0], service)
        self.assertEqual(apply.call_args.kwargs["read_version"]("version.txt"), "1.0")
        self.assertIs(apply.call_args.kwargs["fetch_device_info"].__self__, initializer)

    def test_fetch_device_info_normalizes_mapping_to_plain_dict(self) -> None:
        service = _runtime_service()
        with patch(
            "venus_evcharger.bootstrap.runtime.fetch_device_info_with_fallback",
            return_value={"mac": "ABC"},
        ) as fetch:
            result = _initializer(service).fetch_device_info_with_fallback()

        self.assertEqual(result, {"mac": "ABC"})
        fetch.assert_called_once_with(service)

    def test_start_runtime_loops_delegates_only_after_runtime_contract_is_complete(self) -> None:
        service = _runtime_service()
        initializer = _initializer(service)
        with patch("venus_evcharger.bootstrap.runtime.start_runtime_loops") as start:
            initializer.start_runtime_loops()

        start.assert_called_once()
        self.assertIs(start.call_args.args[0], service)

    def test_start_runtime_loops_rejects_incomplete_service(self) -> None:
        with self.assertRaisesRegex(TypeError, "RuntimeLoopService"):
            _initializer(SimpleNamespace()).start_runtime_loops()
