# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from venus_evcharger.backend.cerbo_gx_relay_switch import (
    CerboGxRelaySwitchBackend,
    load_cerbo_gx_relay_switch_settings,
)
from venus_evcharger.backend.registry import create_switch_backend


class _FakeBusItem:
    def __init__(self, bus: "_FakeBus", service: str, path: str) -> None:
        self.bus = bus
        self.service = service
        self.path = path

    def GetValue(self) -> object:
        self.bus.get_calls.append((self.service, self.path))
        if self.bus.fail_next_get:
            self.bus.fail_next_get = False
            raise RuntimeError("dbus down")
        if (self.service, self.path) in self.bus.missing_get_paths:
            raise KeyError(self.path)
        return self.bus.values.get((self.service, self.path), 0)

    def SetValue(self, value: int) -> object:
        self.bus.set_calls.append((self.service, self.path, int(value)))
        if (self.service, self.path) in self.bus.ignore_set_paths:
            return self.bus.set_result
        self.bus.values[(self.service, self.path)] = int(value)
        return self.bus.set_result


class _FakeBus:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], object] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, int]] = []
        self.set_result: object = 0
        self.fail_next_get = False
        self.missing_get_paths: set[tuple[str, str]] = set()
        self.ignore_set_paths: set[tuple[str, str]] = set()

    def get_object(self, service: str, path: str, introspect: bool = True) -> _FakeBusItem:
        return _FakeBusItem(self, service, path)


class TestCerboGxRelaySwitchBackend(unittest.TestCase):
    def _config(self, text: str) -> str:
        temp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        with temp:
            temp.write(text)
        return temp.name

    def _service(self, bus: _FakeBus) -> SimpleNamespace:
        return SimpleNamespace(
            _get_system_bus=lambda: bus,
            _reset_system_bus=lambda: setattr(bus, "reset_called", True),
            requested_phase_selection="P1",
        )

    def test_no_contact_sets_manual_function_and_relay_on(self) -> None:
        config_path = self._config(
            "[Adapter]\n"
            "Type=cerbo_gx_relay_switch\n"
            "RelayIndex=0\n"
            "ContactMode=NO\n"
            "VerifySettleSeconds=0\n"
            "VerifyRetrySeconds=0\n"
        )
        bus = _FakeBus()
        bus.values[("com.victronenergy.settings", "/Settings/Relay/0/Function")] = 0
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)

        backend.set_enabled(True)
        state = backend.read_switch_state()

        self.assertTrue(state.enabled)
        self.assertEqual(state.phase_selection, "P1")
        self.assertIn(("com.victronenergy.settings", "/Settings/Relay/0/Function", 2), bus.set_calls)
        self.assertIn(("com.victronenergy.system", "/Relay/0/State", 1), bus.set_calls)

    def test_nc_contact_inverts_enabled_mapping_and_supports_relay_two(self) -> None:
        config_path = self._config(
            "[Adapter]\n"
            "Type=cerbo_gx_relay_switch\n"
            "RelayIndex=1\n"
            "ContactMode=NC\n"
            "VerifySettleSeconds=0\n"
            "VerifyRetrySeconds=0\n"
        )
        bus = _FakeBus()
        bus.values[("com.victronenergy.settings", "/Settings/Relay/1/Function")] = 2
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)

        backend.set_enabled(True)
        self.assertTrue(backend.read_switch_state().enabled)
        backend.set_enabled(False)

        self.assertIn(("com.victronenergy.system", "/Relay/1/State", 0), bus.set_calls)
        self.assertIn(("com.victronenergy.system", "/Relay/1/State", 1), bus.set_calls)
        self.assertNotIn(("com.victronenergy.settings", "/Settings/Relay/Function", 2), bus.set_calls)

    def test_relay_zero_uses_legacy_manual_function_fallback(self) -> None:
        config_path = self._config(
            "[Adapter]\n"
            "Type=cerbo_gx_relay_switch\n"
            "RelayIndex=0\n"
            "VerifySettleSeconds=0\n"
            "VerifyRetrySeconds=0\n"
        )
        bus = _FakeBus()
        bus.missing_get_paths.add(("com.victronenergy.settings", "/Settings/Relay/0/Function"))
        bus.values[("com.victronenergy.settings", "/Settings/Relay/Function")] = 0
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)

        backend.set_enabled(False)

        self.assertIn(("com.victronenergy.settings", "/Settings/Relay/Function", 2), bus.set_calls)

    def test_relay_zero_uses_legacy_manual_function_when_specific_path_readback_does_not_update(self) -> None:
        config_path = self._config(
            "[Adapter]\n"
            "Type=cerbo_gx_relay_switch\n"
            "RelayIndex=0\n"
            "VerifySettleSeconds=0\n"
            "VerifyRetrySeconds=0\n"
        )
        bus = _FakeBus()
        bus.values[("com.victronenergy.settings", "/Settings/Relay/0/Function")] = 0
        bus.values[("com.victronenergy.settings", "/Settings/Relay/Function")] = 0
        bus.ignore_set_paths.add(("com.victronenergy.settings", "/Settings/Relay/0/Function"))
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)

        backend.set_enabled(False)

        self.assertIn(("com.victronenergy.settings", "/Settings/Relay/0/Function", 2), bus.set_calls)
        self.assertIn(("com.victronenergy.settings", "/Settings/Relay/Function", 2), bus.set_calls)

    def test_set_retries_when_verify_readback_still_differs_once(self) -> None:
        config_path = self._config(
            "[Adapter]\n"
            "Type=cerbo_gx_relay_switch\n"
            "RelayIndex=0\n"
            "EnsureManualFunction=0\n"
            "VerifySettleSeconds=0\n"
            "VerifyRetrySeconds=0\n"
        )
        bus = _FakeBus()
        original_get_object = bus.get_object
        reads = {"count": 0}

        def get_object(service: str, path: str, introspect: bool = True) -> _FakeBusItem:
            item = original_get_object(service, path)
            if service == "com.victronenergy.system" and path == "/Relay/0/State":
                original_get = item.GetValue

                def get_value() -> object:
                    reads["count"] += 1
                    if reads["count"] == 1:
                        return 0
                    return original_get()

                item.GetValue = get_value  # type: ignore[method-assign]
            return item

        bus.get_object = get_object  # type: ignore[method-assign]
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)

        backend.set_enabled(True)

        relay_sets = [call for call in bus.set_calls if call[:2] == ("com.victronenergy.system", "/Relay/0/State")]
        self.assertEqual(relay_sets, [("com.victronenergy.system", "/Relay/0/State", 1)] * 2)

    def test_config_validation_and_registry(self) -> None:
        with self.assertRaises(ValueError):
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nRelayIndex=2\n"))
        with self.assertRaises(ValueError):
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nRelayIndex=x\n"))
        with self.assertRaises(ValueError):
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nContactMode=bad\n"))
        with self.assertRaises(FileNotFoundError):
            load_cerbo_gx_relay_switch_settings("/tmp/venus-evcharger-missing-cerbo-relay.ini")

        settings = load_cerbo_gx_relay_switch_settings(
            self._config("[Adapter]\nVerifySettleSeconds=-1\nVerifyRetrySeconds=bad\n")
        )
        self.assertEqual(settings.verify_settle_seconds, 0.1)
        self.assertEqual(settings.verify_retry_seconds, 0.2)

        backend = create_switch_backend("cerbo_gx_relay_switch", self._service(_FakeBus()), "")
        self.assertIsInstance(backend, CerboGxRelaySwitchBackend)
        self.assertEqual(backend.capabilities().switching_mode, "contactor")

    def test_dbus_retry_resets_service_bus(self) -> None:
        config_path = self._config("[Adapter]\nType=cerbo_gx_relay_switch\nEnsureManualFunction=0\n")
        bus = _FakeBus()
        bus.fail_next_get = True
        service = self._service(bus)
        backend = CerboGxRelaySwitchBackend(service, config_path)

        backend.read_switch_state()

        self.assertTrue(getattr(bus, "reset_called", False))

    def test_default_config_and_phase_validation(self) -> None:
        backend = CerboGxRelaySwitchBackend(self._service(_FakeBus()), "")
        self.assertEqual(backend.settings.relay_index, 0)
        backend.set_phase_selection("P1")
        with self.assertRaises(ValueError):
            backend.set_phase_selection("P1_P2")

    def test_set_enabled_raises_when_relay_write_fails(self) -> None:
        config_path = self._config(
            "[Adapter]\nEnsureManualFunction=0\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n"
        )
        bus = _FakeBus()
        bus.set_result = False
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)

        with self.assertRaisesRegex(RuntimeError, "DBus SetValue failed"):
            backend.set_enabled(True)

    def test_verify_retries_with_sleep_and_raises_when_readback_stays_wrong(self) -> None:
        config_path = self._config(
            "[Adapter]\nEnsureManualFunction=0\nVerifySettleSeconds=0.01\nVerifyRetrySeconds=0.02\n"
        )
        bus = _FakeBus()
        bus.ignore_set_paths.add(("com.victronenergy.system", "/Relay/0/State"))
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)

        with patch("venus_evcharger.backend.cerbo_gx_relay_switch.time.sleep") as sleep_mock:
            with self.assertRaisesRegex(RuntimeError, "stayed at"):
                backend.set_enabled(True)

        self.assertEqual([call.args[0] for call in sleep_mock.call_args_list], [0.01, 0.02, 0.01])

    def test_manual_function_failure_paths_are_reported(self) -> None:
        config_path = self._config("[Adapter]\nRelayIndex=0\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n")
        bus = _FakeBus()
        bus.set_result = False
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)
        with self.assertRaisesRegex(RuntimeError, "Unable to set Cerbo GX relay"):
            backend.set_enabled(True)

        bus = _FakeBus()
        bus.missing_get_paths.update(
            {
                ("com.victronenergy.settings", "/Settings/Relay/0/Function"),
                ("com.victronenergy.settings", "/Settings/Relay/Function"),
            }
        )
        backend = CerboGxRelaySwitchBackend(self._service(bus), config_path)
        with self.assertRaisesRegex(RuntimeError, "Unable to set Cerbo GX relay"):
            backend.set_enabled(True)

    def test_dbus_helpers_cover_native_bus_interface_and_value_shapes(self) -> None:
        config_path = self._config("[Adapter]\nEnsureManualFunction=0\n")
        bus = _FakeBus()
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), config_path)

        class _FakeDbusModule:
            class SystemBus:
                def get_object(self, service: str, path: str, introspect: bool = True) -> _FakeBusItem:
                    return bus.get_object(service, path)

            @staticmethod
            def Interface(obj: object, _name: str) -> object:
                return obj

        with patch.dict("sys.modules", {"dbus": _FakeDbusModule}):
            self.assertIsInstance(backend._system_bus(), _FakeDbusModule.SystemBus)
            self.assertIsInstance(backend._busitem("svc", "/path"), _FakeBusItem)

        raw_obj = object()

        class _ObjectBus:
            def get_object(self, _service: str, _path: str, introspect: bool = True) -> object:
                return raw_obj

        backend = CerboGxRelaySwitchBackend(
            SimpleNamespace(_get_system_bus=lambda: _ObjectBus(), requested_phase_selection="P1"),
            config_path,
        )
        with patch.dict("sys.modules", {"dbus": _FakeDbusModule}):
            self.assertIs(backend._busitem("svc", "/path"), raw_obj)

        class _TextValue:
            def __str__(self) -> str:
                return "text-value"

        self.assertEqual(backend._normalized_dbus_value("x"), "x")
        self.assertEqual(backend._normalized_dbus_value(1), 1)
        self.assertEqual(backend._normalized_dbus_value(_TextValue()), "text-value")

    def test_dbus_retry_without_reset_and_setvalue_result_shapes(self) -> None:
        config_path = self._config("[Adapter]\nEnsureManualFunction=0\n")
        bus = _FakeBus()
        calls = {"count": 0}

        def flaky_bus() -> _FakeBus:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary")
            return bus

        backend = CerboGxRelaySwitchBackend(SimpleNamespace(_get_system_bus=flaky_bus, requested_phase_selection="P1"), config_path)
        with patch("venus_evcharger.backend.cerbo_gx_relay_switch.time.sleep") as sleep_mock:
            self.assertEqual(backend._dbus_get_value("svc", "/path"), 0)
        sleep_mock.assert_called_once_with(0.1)

        bus.set_result = "0"
        self.assertTrue(backend._dbus_set_value("svc", "/path", 1))
        bus.set_result = ""
        self.assertFalse(backend._dbus_set_value("svc", "/path", 1))

if __name__ == "__main__":
    unittest.main()
