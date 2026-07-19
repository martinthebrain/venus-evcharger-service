# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for storage values, discovery invalidation, and service guards."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

from tests.support.dbus_inputs import DbusInputServiceFake, GatewayReaderFake
from venus_evcharger.dbus_gateway import (
    GRID_POWER_READ_KEY,
    dbus_path_key,
    gateway_paths,
    write_json_file,
)
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.dbus import DbusInputController
from venus_evcharger.inputs.storage_support import EnergyServiceResolver
from venus_evcharger.ports.dbus import DbusInputPort, DbusInputService
from venus_evcharger.service.composition_guards import (
    _DBUS_INPUT_RUNTIME_METHODS,
    _DBUS_INPUT_SERVICE_STATE_FIELDS,
    is_dbus_input_service,
)


def _cache_payload(values: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "captured_at": time.time(),
        "services": [],
        "values": values,
    }


def _service_shape() -> SimpleNamespace:
    service = DbusInputServiceFake()
    attributes = {
        name: getattr(service, name)
        for name in _DBUS_INPUT_SERVICE_STATE_FIELDS
    }
    return SimpleNamespace(runtime=service.runtime, **attributes)


class StorageGatewayValueContractTests(unittest.TestCase):
    def test_raw_text_survives_gateway_and_controller_without_relaxing_semantic_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            raw_key = dbus_path_key("hybrid.a", "/Mode")
            soc_key = dbus_path_key("hybrid.a", "/Soc")
            write_json_file(
                paths.cache_path,
                _cache_payload(
                    {
                        raw_key: {
                            "value": "  self-consumption  ",
                            "status": "fresh",
                            "updated_at": time.time(),
                        },
                        soc_key: {
                            "value": 55.0,
                            "status": "fresh",
                            "updated_at": time.time(),
                        },
                        GRID_POWER_READ_KEY: {
                            "value": "self-consumption",
                            "status": "fresh",
                            "updated_at": time.time(),
                        },
                    }
                ),
            )
            source = EnergySourceDefinition(
                "hybrid",
                service_name="hybrid.a",
                soc_path="/Soc",
                operating_mode_path="/Mode",
            )
            service = DbusInputServiceFake(
                dbus_gateway_cache_path=paths.cache_path,
                dbus_gateway_run_dir=temp_dir,
                auto_energy_sources=(source,),
            )
            port = DbusInputPort(service)
            controller = DbusInputController(port)

            self.assertEqual(
                controller.get_dbus_value("hybrid.a", "/Mode"),
                "  self-consumption  ",
            )
            self.assertEqual(
                controller.storage._dbus_energy_source_snapshot(source, 123.0).operating_mode,
                "self-consumption",
            )
            self.assertIsNone(
                controller.gateway.read_semantic_value(
                    GRID_POWER_READ_KEY,
                    reason="numeric semantic contract",
                )
            )
            self.assertEqual(service.runtime.failures, ["dbus"])
            self.assertEqual(len(list(Path(paths.command_dir).glob("*.json"))), 1)


class EnergyServiceInvalidationContractTests(unittest.TestCase):
    def test_unconditional_invalidation_uses_current_cache_and_unknown_source_is_noop(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.", soc_path="/Soc")
        secondary = EnergySourceDefinition("secondary", service_prefix="hybrid.", soc_path="/Soc")
        service = DbusInputServiceFake(
            auto_energy_sources=(primary, secondary),
            _resolved_auto_energy_services={"secondary": "hybrid.old"},
            _auto_energy_last_scan={"secondary": 91.0},
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), GatewayReaderFake())

        self.assertFalse(resolver.invalidate_energy_source_service("unknown"))
        self.assertEqual(service._resolved_auto_energy_services, {"secondary": "hybrid.old"})
        self.assertTrue(resolver.invalidate_energy_source_service("secondary"))
        self.assertEqual(service._resolved_auto_energy_services, {})
        self.assertEqual(service._auto_energy_last_scan, {})

    def test_secondary_invalidation_is_conditional_and_clears_both_source_maps(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.", soc_path="/Soc")
        secondary = EnergySourceDefinition("secondary", service_prefix="hybrid.", soc_path="/Soc")
        service = DbusInputServiceFake(
            auto_energy_sources=(primary, secondary),
            _resolved_auto_battery_service="battery.a",
            _auto_battery_last_scan=90.0,
            _resolved_auto_energy_services={
                "primary": "battery.a",
                "secondary": "hybrid.old",
            },
            _auto_energy_last_scan={"primary": 90.0, "secondary": 91.0},
        )
        port = DbusInputPort(service)
        DbusInputController(port)

        self.assertFalse(
            port.invalidate_energy_source_service(
                "secondary",
                expected_service="hybrid.new",
            )
        )
        self.assertFalse(
            port.invalidate_energy_source_service(
                "secondary",
                expected_service="  ",
            )
        )
        self.assertEqual(service._resolved_auto_energy_services["secondary"], "hybrid.old")
        self.assertTrue(
            port.invalidate_energy_source_service(
                "secondary",
                expected_service="hybrid.old",
            )
        )
        self.assertNotIn("secondary", service._resolved_auto_energy_services)
        self.assertNotIn("secondary", service._auto_energy_last_scan)
        self.assertEqual(service._resolved_auto_energy_services, {"primary": "battery.a"})
        self.assertEqual(service._resolved_auto_battery_service, "battery.a")

    def test_unreadable_cached_secondary_is_replaced_by_discovery(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.", soc_path="/Soc")
        secondary = EnergySourceDefinition("secondary", service_prefix="hybrid.", soc_path="/Soc")
        service = DbusInputServiceFake(
            auto_battery_scan_interval_seconds=30.0,
            auto_energy_sources=(primary, secondary),
            _resolved_auto_energy_services={"secondary": "hybrid.old"},
            _auto_energy_last_scan={"secondary": 99.0},
        )
        gateway = GatewayReaderFake(
            services=["hybrid.old", "hybrid.new"],
            raw_results={
                ("hybrid.old", "/Soc"): [None],
                ("hybrid.new", "/Soc"): [55.0],
            },
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), gateway)

        with (
            patch("venus_evcharger.inputs.storage_support.time.time", return_value=100.0),
            patch.object(
                resolver,
                "invalidate_energy_source_service",
                wraps=resolver.invalidate_energy_source_service,
            ) as invalidate,
        ):
            self.assertEqual(resolver.resolve_energy_source_service(secondary), "hybrid.new")

        invalidate.assert_called_once_with(
            "secondary",
            expected_service="hybrid.old",
        )
        self.assertEqual(service._resolved_auto_energy_services, {"secondary": "hybrid.new"})
        self.assertEqual(service._auto_energy_last_scan, {"secondary": 100.0})
        self.assertEqual(gateway.service_list_calls, 1)

    def test_invalidation_uses_race_tolerant_cache_removal(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.", soc_path="/Soc")
        secondary = EnergySourceDefinition("secondary", service_prefix="hybrid.", soc_path="/Soc")
        service = DbusInputServiceFake(auto_energy_sources=(primary, secondary))
        resolved_cache = MagicMock(spec=dict)
        scan_cache = MagicMock(spec=dict)
        service._resolved_auto_energy_services = cast(dict[str, str], resolved_cache)
        service._auto_energy_last_scan = cast(dict[str, float], scan_cache)
        resolver = EnergyServiceResolver(DbusInputPort(service), GatewayReaderFake())

        with patch(
            "venus_evcharger.inputs.storage_support._matching_cached_service",
            return_value="hybrid.old",
        ):
            self.assertTrue(
                resolver.invalidate_energy_source_service(
                    "secondary",
                    expected_service="hybrid.old",
                )
            )

        resolved_cache.pop.assert_called_once_with("secondary", None)
        scan_cache.pop.assert_called_once_with("secondary", None)

    def test_primary_invalidation_clears_primary_and_per_source_caches(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.", soc_path="/Soc")
        service = DbusInputServiceFake(
            auto_energy_sources=(primary,),
            _resolved_auto_battery_service="battery.old",
            _auto_battery_last_scan=80.0,
            _resolved_auto_energy_services={"primary": "battery.old"},
            _auto_energy_last_scan={"primary": 81.0},
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), GatewayReaderFake())

        self.assertTrue(
            resolver.invalidate_energy_source_service(
                "primary",
                expected_service="battery.old",
            )
        )
        self.assertIsNone(service._resolved_auto_battery_service)
        self.assertEqual(service._auto_battery_last_scan, 0.0)
        self.assertEqual(service._resolved_auto_energy_services, {})
        self.assertEqual(service._auto_energy_last_scan, {})

    def test_primary_invalidation_preserves_a_newer_compatibility_cache(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.", soc_path="/Soc")
        service = DbusInputServiceFake(
            auto_energy_sources=(primary,),
            _resolved_auto_battery_service="battery.current",
            _auto_battery_last_scan=90.0,
            _resolved_auto_energy_services={"primary": "battery.old"},
            _auto_energy_last_scan={"primary": 81.0},
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), GatewayReaderFake())

        self.assertTrue(
            resolver.invalidate_energy_source_service(
                "primary",
                expected_service="battery.old",
            )
        )
        self.assertEqual(service._resolved_auto_battery_service, "battery.current")
        self.assertEqual(service._auto_battery_last_scan, 90.0)
        self.assertEqual(service._resolved_auto_energy_services, {})
        self.assertEqual(service._auto_energy_last_scan, {})

    def test_primary_compatibility_invalidation_also_clears_per_source_cache(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.", soc_path="/Soc")
        service = DbusInputServiceFake(
            auto_energy_sources=(primary,),
            _resolved_auto_battery_service="battery.old",
            _auto_battery_last_scan=80.0,
            _resolved_auto_energy_services={"primary": "battery.old"},
            _auto_energy_last_scan={"primary": 81.0},
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), GatewayReaderFake())

        resolver.invalidate_auto_battery_service()

        self.assertIsNone(service._resolved_auto_battery_service)
        self.assertEqual(service._auto_battery_last_scan, 0.0)
        self.assertEqual(service._resolved_auto_energy_services, {})
        self.assertEqual(service._auto_energy_last_scan, {})


class DbusInputServiceGuardContractTests(unittest.TestCase):
    def test_guard_field_list_matches_declared_service_state(self) -> None:
        self.assertEqual(
            set(_DBUS_INPUT_SERVICE_STATE_FIELDS),
            set(DbusInputService.__annotations__),
        )

    def test_guard_requires_every_declared_state_field(self) -> None:
        self.assertTrue(is_dbus_input_service(_service_shape()))
        for field_name in _DBUS_INPUT_SERVICE_STATE_FIELDS:
            with self.subTest(field_name=field_name):
                service = _service_shape()
                delattr(service, field_name)
                self.assertFalse(is_dbus_input_service(service))

    def test_guard_requires_every_runtime_operation_to_be_callable(self) -> None:
        for method_name in _DBUS_INPUT_RUNTIME_METHODS:
            with self.subTest(method_name=method_name):
                service = _service_shape()
                setattr(service.runtime, method_name, None)
                self.assertFalse(is_dbus_input_service(service))


if __name__ == "__main__":
    unittest.main()
