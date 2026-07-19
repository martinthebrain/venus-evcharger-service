# SPDX-License-Identifier: GPL-3.0-or-later
"""Precise resolver and cache contracts for DBus input storage."""

from __future__ import annotations

import unittest
from unittest.mock import call, patch

from tests.support.dbus_inputs import DbusInputServiceFake, GatewayReaderFake
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs import storage_support as storage_support_module
from venus_evcharger.inputs.storage_support import (
    EnergyServiceResolver,
    _energy_cache_entry,
)
from venus_evcharger.ports.dbus import DbusInputPort


class StorageSupportMutationContractTests(unittest.TestCase):
    @staticmethod
    def _resolver(
        service: DbusInputServiceFake | None = None,
        gateway: GatewayReaderFake | None = None,
    ) -> EnergyServiceResolver:
        return EnergyServiceResolver(
            DbusInputPort(service or DbusInputServiceFake()),
            gateway or GatewayReaderFake(),
        )

    def test_default_primary_source_maps_every_service_setting(self) -> None:
        service = DbusInputServiceFake(
            auto_battery_service="battery.fixed",
            auto_battery_service_prefix="battery.",
            auto_battery_soc_path="/StateOfCharge",
            auto_battery_capacity_wh=12345.0,
            auto_battery_power_path="/BatteryPower",
            auto_battery_ac_power_path="/AcPower",
            auto_battery_pv_power_path="/PvPower",
            auto_battery_grid_interaction_path="/GridPower",
            auto_battery_operating_mode_path="/OperatingMode",
        )
        self.assertEqual(
            self._resolver(service)._default_primary_energy_source(),
            EnergySourceDefinition(
                source_id="primary_battery",
                service_name="battery.fixed",
                service_prefix="battery.",
                soc_path="/StateOfCharge",
                usable_capacity_wh=12345.0,
                battery_power_path="/BatteryPower",
                ac_power_path="/AcPower",
                pv_power_path="/PvPower",
                grid_interaction_path="/GridPower",
                operating_mode_path="/OperatingMode",
            ),
        )

    def test_energy_cache_entry_accepts_only_named_service_and_numeric_timestamp(self) -> None:
        service = DbusInputServiceFake(
            _resolved_auto_energy_services={"a": "  battery.a  "},
            _auto_energy_last_scan={"a": 12.5},
        )
        self.assertEqual(_energy_cache_entry(service, "a"), ("battery.a", 12.5))
        for malformed_service in (None, 1, " "):
            object.__setattr__(service, "_resolved_auto_energy_services", {"a": malformed_service})
            self.assertIsNone(_energy_cache_entry(service, "a"))
        service._resolved_auto_energy_services = {"a": "battery.a"}
        for malformed_time in (None, True):
            object.__setattr__(service, "_auto_energy_last_scan", {"a": malformed_time})
            self.assertIsNone(_energy_cache_entry(service, "a"))

    def test_battery_soc_probe_uses_exact_path_and_introspection_request(self) -> None:
        service = DbusInputServiceFake(auto_battery_soc_path="/ExactSoc")
        gateway = GatewayReaderFake(raw_results={("battery.a", "/ExactSoc"): [0.0]})
        resolver = self._resolver(service, gateway)
        with patch.object(resolver, "introspection_says_skip", return_value=False) as skip:
            self.assertTrue(resolver._battery_service_has_soc("battery.a"))
        skip.assert_called_once_with("battery.a", "/ExactSoc", priority=80)
        self.assertEqual(gateway.raw_reads, [("battery.a", "/ExactSoc")])

        failing = self._resolver(
            service,
            GatewayReaderFake(raw_results={("battery.a", "/ExactSoc"): [OSError("offline")]}),
        )
        with (
            patch.object(failing, "introspection_says_skip", return_value=False),
            patch.object(failing, "_request_introspection") as request,
        ):
            self.assertFalse(failing._battery_service_has_soc("battery.a"))
        request.assert_called_once_with(
            "battery.a", "/ExactSoc", priority=95, reason="battery SOC probe failed"
        )

    def test_generic_field_probe_distinguishes_empty_skip_none_value_and_error(self) -> None:
        resolver = self._resolver(gateway=GatewayReaderFake(raw_results={("battery.a", "/Mode"): [None]}))
        self.assertFalse(resolver._energy_service_has_readable_field("battery.a", ""))
        with patch.object(resolver, "introspection_says_skip", return_value=True) as skip:
            self.assertFalse(resolver._energy_service_has_readable_field("battery.a", "/Skipped"))
        skip.assert_called_once_with("battery.a", "/Skipped", priority=85)
        with patch.object(resolver, "introspection_says_skip", return_value=False):
            self.assertFalse(resolver._energy_service_has_readable_field("battery.a", "/Mode"))

        failing = self._resolver(
            gateway=GatewayReaderFake(raw_results={("battery.a", "/Mode"): [RuntimeError("offline")]}),
        )
        with (
            patch.object(failing, "introspection_says_skip", return_value=False),
            patch.object(failing, "_request_introspection") as request,
        ):
            self.assertFalse(failing._energy_service_has_readable_field("battery.a", "/Mode"))
        request.assert_called_once_with(
            "battery.a", "/Mode", priority=95, reason="energy-source field probe failed"
        )

    def test_override_resolution_short_circuits_in_priority_order(self) -> None:
        source = EnergySourceDefinition("primary", service_name="battery.fixed")
        service = DbusInputServiceFake(auto_energy_sources=(source,))
        resolver = self._resolver(service)
        with (
            patch.object(resolver, "_battery_service_has_soc", return_value=True) as has_soc,
            patch.object(resolver, "_energy_source_has_readable_data") as has_data,
        ):
            self.assertEqual(resolver._resolve_battery_service_override(), "battery.fixed")
        has_soc.assert_called_once_with("battery.fixed")
        has_data.assert_not_called()

        with (
            patch.object(resolver, "_battery_service_has_soc", return_value=False) as has_soc,
            patch.object(resolver, "_energy_source_has_readable_data", return_value=True) as has_data,
        ):
            self.assertEqual(resolver._resolve_battery_service_override(), "battery.fixed")
        has_soc.assert_called_once_with("battery.fixed")
        has_data.assert_called_once_with(source, "battery.fixed")

        with (
            patch.object(resolver, "_battery_service_has_soc", return_value=False),
            patch.object(resolver, "_energy_source_has_readable_data", return_value=False),
            patch("venus_evcharger.inputs.storage_support.logging.debug") as debug,
        ):
            self.assertIsNone(resolver._resolve_battery_service_override())
        debug.assert_called_once_with(
            "Auto battery service override %s missing SOC, falling back to prefix scan.",
            "battery.fixed",
        )
        self.assertIsNone(self._resolver()._resolve_battery_service_override())

    def test_cache_validation_forwards_exact_values_and_remember_updates_both_maps(self) -> None:
        service = DbusInputServiceFake(
            auto_battery_scan_interval_seconds=45.0,
            _resolved_auto_battery_service="battery.cached",
            _auto_battery_last_scan=90.0,
            _resolved_auto_energy_services={"secondary": "hybrid.cached"},
            _auto_energy_last_scan={"secondary": 91.0},
        )
        resolver = self._resolver(service)
        with patch.object(storage_support_module, "discovery_cache_valid", return_value=True) as valid:
            self.assertEqual(resolver._cached_auto_battery_service(100.0), "battery.cached")
            self.assertEqual(resolver._energy_cache_valid("secondary", 101.0), "hybrid.cached")
        self.assertEqual(
            valid.call_args_list,
            [
                call("battery.cached", 90.0, 45.0, 100.0),
                call("hybrid.cached", 91.0, 45.0, 101.0),
            ],
        )
        self.assertEqual(resolver._remember_energy_service("new", "inverter.new", 102.0), "inverter.new")
        self.assertEqual(service._resolved_auto_energy_services["new"], "inverter.new")
        self.assertEqual(service._auto_energy_last_scan["new"], 102.0)

    def test_configured_and_discovered_services_preserve_resolution_contract(self) -> None:
        resolver = self._resolver()
        unconfigured = EnergySourceDefinition("secondary")
        with patch.object(resolver, "_energy_source_has_readable_data") as has_data:
            self.assertIsNone(resolver._configured_energy_source_service(unconfigured, 40.0))
        has_data.assert_not_called()

        configured = EnergySourceDefinition("secondary", service_name="hybrid.fixed")
        with (
            patch.object(resolver, "_energy_source_has_readable_data", return_value=True) as has_data,
            patch.object(resolver, "_remember_energy_service", return_value="hybrid.fixed") as remember,
        ):
            self.assertEqual(
                resolver._configured_energy_source_service(configured, 41.5),
                "hybrid.fixed",
            )
        has_data.assert_called_once_with(configured, "hybrid.fixed")
        remember.assert_called_once_with("secondary", "hybrid.fixed", 41.5)

        discovered = EnergySourceDefinition("secondary", service_prefix="hybrid.")
        gateway = GatewayReaderFake(services=["other", "hybrid.detected"])
        resolver = self._resolver(gateway=gateway)
        with (
            patch.object(resolver, "_energy_source_has_readable_data", return_value=True) as has_data,
            patch.object(resolver, "_remember_energy_service", return_value="hybrid.detected") as remember,
        ):
            self.assertEqual(
                resolver._discovered_energy_source_service(discovered, 42.5),
                "hybrid.detected",
            )
        self.assertEqual(gateway.service_list_calls, 1)
        has_data.assert_called_once_with(discovered, "hybrid.detected")
        remember.assert_called_once_with("secondary", "hybrid.detected", 42.5)

    def test_secondary_resolution_uses_config_cache_then_discovery(self) -> None:
        primary = EnergySourceDefinition("primary")
        secondary = EnergySourceDefinition("secondary", service_prefix="hybrid.")
        resolver = self._resolver()
        with (
            patch.object(resolver, "primary_energy_source", return_value=primary),
            patch.object(resolver, "_configured_energy_source_service", return_value=None) as configured,
            patch.object(resolver, "_energy_cache_valid", return_value="hybrid.cached") as cached,
            patch.object(resolver, "_energy_source_has_readable_data", return_value=True) as readable,
            patch.object(resolver, "_discovered_energy_source_service") as discovered,
            patch("venus_evcharger.inputs.storage_support.time.time", return_value=100.0),
        ):
            self.assertEqual(resolver.resolve_energy_source_service(secondary), "hybrid.cached")
        configured.assert_called_once_with(secondary, 100.0)
        cached.assert_called_once_with("secondary", 100.0)
        readable.assert_called_once_with(secondary, "hybrid.cached")
        discovered.assert_not_called()

        with (
            patch.object(resolver, "primary_energy_source", return_value=primary),
            patch.object(resolver, "_configured_energy_source_service", return_value=None),
            patch.object(resolver, "_energy_cache_valid", return_value=None),
            patch.object(resolver, "_discovered_energy_source_service", return_value="hybrid.new") as discovered,
            patch("venus_evcharger.inputs.storage_support.time.time", return_value=101.0),
        ):
            self.assertEqual(resolver.resolve_energy_source_service(secondary), "hybrid.new")
        discovered.assert_called_once_with(secondary, 101.0)

    def test_scan_and_auto_resolution_store_exact_cache_state(self) -> None:
        service = DbusInputServiceFake(auto_battery_service_prefix="battery.")
        gateway = GatewayReaderFake(
            services=["other", "battery.a"],
            raw_results={("battery.a", "/Soc"): [50.0]},
        )
        resolver = self._resolver(service, gateway)
        self.assertEqual(resolver._scan_auto_battery_service(200.0), "battery.a")
        self.assertEqual(service._resolved_auto_battery_service, "battery.a")
        self.assertEqual(service._auto_battery_last_scan, 200.0)
        self.assertEqual(service._resolved_auto_energy_services, {"primary_battery": "battery.a"})
        self.assertEqual(service._auto_energy_last_scan, {"primary_battery": 200.0})

        ordered = self._resolver()
        with (
            patch.object(ordered, "_resolve_battery_service_override", return_value=None) as override,
            patch.object(ordered, "_cached_auto_battery_service", return_value=None) as cached,
            patch.object(ordered, "_scan_auto_battery_service", return_value="battery.scan") as scan,
            patch("venus_evcharger.inputs.storage_support.time.time", return_value=201.0),
        ):
            self.assertEqual(ordered.resolve_auto_battery_service(), "battery.scan")
        override.assert_called_once_with()
        cached.assert_called_once_with(201.0)
        scan.assert_called_once_with(201.0)

    def test_scan_prefers_source_prefix_and_emits_resolved_service_diagnostic(self) -> None:
        source = EnergySourceDefinition(
            "primary",
            service_prefix="source-specific.",
            soc_path="/Soc",
        )
        service = DbusInputServiceFake(
            auto_battery_service_prefix="fallback.",
            auto_energy_sources=(source,),
        )
        gateway = GatewayReaderFake(
            services=["fallback.a", "source-specific.a"],
            raw_results={("source-specific.a", "/Soc"): [55.0]},
        )
        resolver = self._resolver(service, gateway)
        with patch("venus_evcharger.inputs.storage_support.logging.debug") as debug:
            self.assertEqual(resolver._scan_auto_battery_service(300.0), "source-specific.a")
        debug.assert_called_once_with(
            "Auto battery service resolved: %s",
            "source-specific.a",
        )

    def test_invalidation_and_introspection_refresh_have_exact_side_effects(self) -> None:
        service = DbusInputServiceFake(
            _resolved_auto_battery_service="battery.a",
            _auto_battery_last_scan=99.0,
        )
        port = DbusInputPort(service)
        resolver = EnergyServiceResolver(port, GatewayReaderFake())
        resolver.invalidate_auto_battery_service()
        self.assertIsNone(service._resolved_auto_battery_service)
        self.assertEqual(service._auto_battery_last_scan, 0.0)

        with (
            patch.object(port, "path_unusable", side_effect=[(False, ""), (True, "missing")]) as unusable,
            patch.object(port, "request_introspection", return_value=True) as request,
            patch("venus_evcharger.inputs.storage_support.logging.debug") as debug,
        ):
            self.assertFalse(resolver.introspection_says_skip("battery.a", "/Soc", priority=80))
            self.assertTrue(resolver.introspection_says_skip("battery.a", "/Power", priority=91))
        self.assertEqual(
            unusable.call_args_list,
            [call("battery.a", "/Soc"), call("battery.a", "/Power")],
        )
        request.assert_called_once_with(
            "battery.a",
            "/Power",
            priority=91,
            reason="known-unusable input path",
            source="evcharger-inputs",
        )
        debug.assert_called_once_with(
            "Skipping %s %s from DBus introspection cache: %s",
            "battery.a",
            "/Power",
            "missing",
        )
