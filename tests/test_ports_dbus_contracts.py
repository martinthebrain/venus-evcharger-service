# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed contracts for the DBus-input adapter port."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.support.dbus_inputs import DbusInputControllerFake, DbusInputServiceFake
from venus_evcharger.ports.dbus import DbusInputPort


class DbusInputPortContractTests(unittest.TestCase):
    def test_runtime_and_configuration_operations_are_exact(self) -> None:
        service = DbusInputServiceFake(
            dbus_gateway_cache_path=" /cache.json ",
            dbus_gateway_run_dir=" /run/gateway ",
            dbus_gateway_max_age_seconds=-3.0,
        )
        service.runtime.ready["pv"] = False
        port = DbusInputPort(service)
        self.assertFalse(port.source_retry_ready("pv", 1.5))
        port.mark_recovery("pv", "back", 1)
        port.mark_failure("pv")
        port.delay_source_retry("pv", 2.0)
        port.warning_throttled("warn", 3.0, "message %s", "x")
        self.assertEqual(port.gateway_cache_path(), "/cache.json")
        self.assertEqual(port.gateway_run_dir(), "/run/gateway")
        self.assertEqual(port.gateway_max_age_seconds(), 0.0)
        self.assertEqual(service.runtime.failures, ["pv"])

    def test_learning_state_and_bound_controller_are_explicit(self) -> None:
        service = DbusInputServiceFake(dbus_gateway_max_age_seconds=0.0)
        port = DbusInputPort(service)
        with self.assertRaisesRegex(RuntimeError, "not bound"):
            port.list_dbus_services()
        controller = DbusInputControllerFake()
        port.bind_controller(controller)
        self.assertEqual(port.get_dbus_value("a", "/Value"), 12.0)
        self.assertEqual(port.list_dbus_services(), ["service.a"])
        self.assertEqual(port.resolve_auto_pv_services(), ["pv.a"])
        self.assertEqual(port.resolve_auto_battery_service(), "battery.a")
        port.invalidate_auto_pv_services()
        port.invalidate_auto_battery_service()
        self.assertEqual((controller.invalidated_pv, controller.invalidated_battery), (1, 1))
        self.assertEqual(port.gateway_max_age_seconds(), 10.0)
        port.store_energy_learning_profiles({"a": 1})
        port.store_energy_cluster({"soc": 50.0})
        self.assertEqual(port.energy_learning_profiles(), {"a": 1})
        self.assertEqual(service._last_energy_cluster, {"soc": 50.0})

    def test_empty_introspection_boundary_is_advisory(self) -> None:
        port = DbusInputPort(DbusInputServiceFake())
        self.assertEqual(port.path_unusable("service", "/Soc"), (False, ""))
        self.assertFalse(
            port.request_introspection(
                "service",
                "/Soc",
                priority=90,
                reason="probe",
                source="test",
            )
        )

    def test_unbound_controller_error_identifies_the_boundary_exactly(self) -> None:
        port = DbusInputPort(DbusInputServiceFake())

        with self.assertRaisesRegex(
            RuntimeError,
            "^DBus input controller is not bound$",
        ):
            port.list_dbus_services()

    def test_runtime_operations_forward_every_domain_argument(self) -> None:
        service = DbusInputServiceFake()
        service.runtime.ready["storage"] = True
        port = DbusInputPort(service)

        with patch.object(
            service.runtime,
            "source_retry_ready",
            wraps=service.runtime.source_retry_ready,
        ) as retry_ready:
            self.assertTrue(port.source_retry_ready("storage", 12.5))
        retry_ready.assert_called_once_with("storage", 12.5)

        port.mark_recovery("storage", "recovered %s", "now")
        port.delay_source_retry("storage", 14.0)
        port.warning_throttled("storage-warning", 30.0, "failed %s", "once")

        self.assertEqual(
            service.runtime.recoveries,
            [("storage", "recovered %s", ("now",))],
        )
        self.assertEqual(service.runtime.delayed, [("storage", 14.0)])
        self.assertEqual(
            service.runtime.warnings,
            [("storage-warning", 30.0, "failed %s", ("once",))],
        )

    def test_empty_gateway_paths_remain_empty(self) -> None:
        port = DbusInputPort(
            DbusInputServiceFake(
                dbus_gateway_cache_path="",
                dbus_gateway_run_dir="",
            )
        )

        self.assertEqual(port.gateway_cache_path(), "")
        self.assertEqual(port.gateway_run_dir(), "")

    def test_introspection_operations_forward_the_complete_request(self) -> None:
        service = DbusInputServiceFake()
        port = DbusInputPort(service)

        with patch(
            "venus_evcharger.ports.dbus.owner_path_unusable",
            return_value=(True, "missing-path"),
        ) as path_unusable:
            self.assertEqual(
                port.path_unusable("service.a", "/Soc"),
                (True, "missing-path"),
            )
        path_unusable.assert_called_once_with(service, "service.a", "/Soc")

        with patch(
            "venus_evcharger.ports.dbus.request_owner_introspection",
            return_value=True,
        ) as request_introspection:
            self.assertTrue(
                port.request_introspection(
                    "service.a",
                    "/Soc",
                    priority=91,
                    reason="refresh SOC",
                    source="storage-contract",
                )
            )
        request_introspection.assert_called_once_with(
            service,
            "service.a",
            "/Soc",
            priority=91,
            reason="refresh SOC",
            source="storage-contract",
        )

    def test_controller_operations_forward_identity_and_paths(self) -> None:
        controller = DbusInputControllerFake(
            raw_value="self-consumption",
            services=["service.b", "service.a"],
            pv_services=["pv.b", "pv.a"],
            battery_service="battery.a",
        )
        port = DbusInputPort(DbusInputServiceFake())
        port.bind_controller(controller)

        with patch.object(
            controller,
            "get_dbus_value",
            wraps=controller.get_dbus_value,
        ) as get_value:
            self.assertEqual(
                port.get_dbus_value("service.a", "/Mode"),
                "self-consumption",
            )
        get_value.assert_called_once_with("service.a", "/Mode")
        self.assertEqual(port.list_dbus_services(), ["service.b", "service.a"])
        self.assertEqual(port.resolve_auto_pv_services(), ["pv.b", "pv.a"])
        self.assertEqual(port.resolve_auto_battery_service(), "battery.a")

    def test_controller_return_contracts_name_the_failing_operation(self) -> None:
        controller = DbusInputControllerFake()
        port = DbusInputPort(DbusInputServiceFake())
        port.bind_controller(controller)

        with patch.object(
            controller,
            "list_dbus_services",
            return_value=object(),
        ):
            with self.assertRaisesRegex(
                TypeError,
                "^list_dbus_services must return list, got object$",
            ):
                port.list_dbus_services()
        with patch.object(
            controller,
            "resolve_auto_pv_services",
            return_value=object(),
        ):
            with self.assertRaisesRegex(
                TypeError,
                "^resolve_auto_pv_services must return list, got object$",
            ):
                port.resolve_auto_pv_services()
        with patch.object(
            controller,
            "resolve_auto_battery_service",
            return_value=7,
        ):
            with self.assertRaisesRegex(
                TypeError,
                "^resolve_auto_battery_service must return str, got int$",
            ):
                port.resolve_auto_battery_service()


if __name__ == "__main__":
    unittest.main()
