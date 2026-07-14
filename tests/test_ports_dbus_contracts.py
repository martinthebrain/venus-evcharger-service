# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contracts for the DBus-input service port."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from venus_evcharger.ports.dbus import DbusInputPort


def _service_double() -> SimpleNamespace:
    return SimpleNamespace(
        _source_retry_ready=MagicMock(side_effect=[0, "ready"]),
        _mark_recovery=MagicMock(),
        _mark_failure=MagicMock(),
        _delay_source_retry=MagicMock(),
        _warning_throttled=MagicMock(),
        _get_system_bus=MagicMock(return_value="bus"),
        _reset_system_bus=MagicMock(),
        _get_dbus_value=MagicMock(return_value={"raw": True}),
        _list_dbus_services=MagicMock(return_value=["service.a", "service.b"]),
        _invalidate_auto_pv_services=MagicMock(),
        _resolve_auto_pv_services=MagicMock(return_value=["pv.a"]),
        _invalidate_auto_battery_service=MagicMock(),
        _resolve_auto_battery_service=MagicMock(return_value="battery.a"),
    )


class _ControllerDouble:
    def __init__(self) -> None:
        self.get_dbus_value = MagicMock(return_value=17)
        self.list_dbus_services = MagicMock(return_value=["controller.service"])
        self.invalidate_auto_pv_services = MagicMock()
        self.resolve_auto_pv_services = MagicMock(return_value=["controller.pv"])
        self.invalidate_auto_battery_service = MagicMock()
        self.resolve_auto_battery_service = MagicMock(return_value=None)


class DbusInputPortContractTests(unittest.TestCase):
    def test_service_runtime_helpers_forward_exact_arguments_and_results(self) -> None:
        service = _service_double()
        port = DbusInputPort(service)

        self.assertFalse(port.source_retry_ready("pv", 1.5))
        self.assertTrue(port.source_retry_ready("grid", 2.5))
        self.assertEqual(
            service._source_retry_ready.call_args_list,
            [call("pv", 1.5), call("grid", 2.5)],
        )

        port.mark_recovery("pv", "recovered %s", "now")
        port.mark_failure("grid")
        port.delay_source_retry("battery", 3.5)
        port.warning_throttled("offline", 10.0, "missing %s", "meter")
        service._mark_recovery.assert_called_once_with("pv", "recovered %s", "now")
        service._mark_failure.assert_called_once_with("grid")
        service._delay_source_retry.assert_called_once_with("battery", 3.5)
        service._warning_throttled.assert_called_once_with(
            "offline",
            10.0,
            "missing %s",
            "meter",
        )

        self.assertEqual(port.get_system_bus(), "bus")
        port.reset_system_bus()
        service._get_system_bus.assert_called_once_with()
        service._reset_system_bus.assert_called_once_with()

    def test_service_overrides_form_the_complete_dbus_boundary(self) -> None:
        service = _service_double()
        port = DbusInputPort(service)

        self.assertEqual(port.get_dbus_value("service.a", "/Value"), {"raw": True})
        self.assertEqual(port.list_dbus_services(), ["service.a", "service.b"])
        port.invalidate_auto_pv_services()
        self.assertEqual(port.resolve_auto_pv_services(), ["pv.a"])
        port.invalidate_auto_battery_service()
        self.assertEqual(port.resolve_auto_battery_service(), "battery.a")

        service._get_dbus_value.assert_called_once_with("service.a", "/Value")
        service._list_dbus_services.assert_called_once_with()
        service._invalidate_auto_pv_services.assert_called_once_with()
        service._resolve_auto_pv_services.assert_called_once_with()
        service._invalidate_auto_battery_service.assert_called_once_with()
        service._resolve_auto_battery_service.assert_called_once_with()

    def test_bound_controller_is_used_when_service_has_no_override(self) -> None:
        service = SimpleNamespace()
        controller = _ControllerDouble()
        port = DbusInputPort(service)
        port.bind_controller(controller)

        self.assertEqual(port.get_dbus_value("service.b", "/Power"), 17)
        self.assertEqual(port.list_dbus_services(), ["controller.service"])
        port.invalidate_auto_pv_services()
        self.assertEqual(port.resolve_auto_pv_services(), ["controller.pv"])
        port.invalidate_auto_battery_service()
        self.assertIsNone(port.resolve_auto_battery_service())

        controller.get_dbus_value.assert_called_once_with("service.b", "/Power")
        controller.list_dbus_services.assert_called_once_with()
        controller.invalidate_auto_pv_services.assert_called_once_with()
        controller.resolve_auto_pv_services.assert_called_once_with()
        controller.invalidate_auto_battery_service.assert_called_once_with()
        controller.resolve_auto_battery_service.assert_called_once_with()

    def test_collection_and_optional_service_return_contracts_are_strict(self) -> None:
        service = _service_double()
        port = DbusInputPort(service)

        invalid_cases = (
            (service._list_dbus_services, ("service",), "list_dbus_services must return list"),
            (service._list_dbus_services, ["service", 1], "list_dbus_services must return list\\[str\\]"),
            (service._resolve_auto_pv_services, "service", "resolve_auto_pv_services must return list"),
            (
                service._resolve_auto_pv_services,
                ["service", None],
                "resolve_auto_pv_services must return list\\[str\\]",
            ),
            (
                service._resolve_auto_battery_service,
                1,
                "resolve_auto_battery_service must return str, got int",
            ),
        )
        actions = (
            port.list_dbus_services,
            port.list_dbus_services,
            port.resolve_auto_pv_services,
            port.resolve_auto_pv_services,
            port.resolve_auto_battery_service,
        )
        for (mock, value, message), action in zip(invalid_cases, actions, strict=True):
            with self.subTest(value=value):
                mock.return_value = value
                with self.assertRaisesRegex(TypeError, message):
                    action()

        service._resolve_auto_battery_service.return_value = None
        self.assertIsNone(port.resolve_auto_battery_service())


if __name__ == "__main__":
    unittest.main()
