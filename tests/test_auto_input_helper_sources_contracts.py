# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from venus_evcharger.dbus_gateway import BATTERY_SOC_READ_KEY, GRID_POWER_READ_KEY, PV_POWER_READ_KEY
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.helper.sources import BatterySourceReader, empty_battery_snapshot
from venus_evcharger.inputs.helper.sources_dbus_primary import EnergySourceCatalog
from venus_evcharger.inputs.helper.sources_dbus_resolve import EnergyServiceResolver
from venus_evcharger.inputs.helper.sources_pv_grid import PvGridSourceReader
from tests.support.auto_input_helper import FakeCatalog, FakeGateway, helper_settings


class AutoInputHelperSourceContracts(unittest.TestCase):
    def test_semantic_battery_read_normalizes_snapshot(self) -> None:
        gateway = FakeGateway()
        gateway.semantic[BATTERY_SOC_READ_KEY] = 72.5
        snapshot = BatterySourceReader(gateway).battery_snapshot()
        self.assertEqual(snapshot["battery_soc"], 72.5)
        self.assertEqual(snapshot["battery_source_count"], 1)

    def test_missing_invalid_and_backed_off_battery_reads_are_safe(self) -> None:
        gateway = FakeGateway()
        reader = BatterySourceReader(gateway)
        self.assertEqual(reader.battery_snapshot(), empty_battery_snapshot())
        self.assertEqual(gateway.delayed, ["battery"])
        gateway.semantic[BATTERY_SOC_READ_KEY] = 101.0
        self.assertEqual(reader.battery_snapshot(), empty_battery_snapshot())
        gateway.retry_ready["battery"] = False
        self.assertEqual(reader.battery_snapshot(), {"battery_soc": None})

    def test_pv_grid_reads_and_discovery_are_gateway_only(self) -> None:
        gateway = FakeGateway()
        gateway.semantic[PV_POWER_READ_KEY] = 500.0
        gateway.semantic[GRID_POWER_READ_KEY] = -25.0
        gateway.services = ["com.victronenergy.pvinverter.a", "other"]
        reader = PvGridSourceReader(helper_settings(), gateway)
        self.assertEqual(reader.pv_power(), 500.0)
        self.assertEqual(reader.grid_power(), -25.0)
        self.assertEqual(reader.resolve_pv_services(), ["com.victronenergy.pvinverter.a"])
        reader.invalidate_pv_services()
        self.assertEqual(reader._resolved_pv_services, [])

    def test_explicit_and_cached_pv_services_skip_repeated_discovery(self) -> None:
        gateway = FakeGateway()
        explicit = PvGridSourceReader(replace(helper_settings(), auto_pv_service="pv.explicit"), gateway)
        self.assertEqual(explicit.resolve_pv_services(), ["pv.explicit"])
        gateway.services = ["com.victronenergy.pvinverter.one"]
        cached = PvGridSourceReader(helper_settings(), gateway)
        with patch("venus_evcharger.inputs.helper.sources_pv_grid.time.time", side_effect=[10.0, 11.0]):
            self.assertEqual(cached.resolve_pv_services(), ["com.victronenergy.pvinverter.one"])
            gateway.services = []
            self.assertEqual(cached.resolve_pv_services(), ["com.victronenergy.pvinverter.one"])

    def test_missing_pv_and_grid_values_enter_retry_window(self) -> None:
        gateway = FakeGateway()
        reader = PvGridSourceReader(helper_settings(), gateway)
        self.assertIsNone(reader.pv_power())
        self.assertIsNone(reader.grid_power())
        self.assertEqual(gateway.delayed, ["pv", "grid"])
        gateway.retry_ready = {"pv": False, "grid": False}
        self.assertIsNone(reader.pv_power())
        self.assertIsNone(reader.grid_power())

    def test_catalog_uses_typed_configured_or_default_source(self) -> None:
        gateway = FakeGateway()
        settings = replace(helper_settings(), auto_energy_sources=())
        catalog = EnergySourceCatalog(settings, gateway)
        primary = catalog.primary_source()
        self.assertEqual(primary.source_id, "primary_battery")
        self.assertEqual(primary.soc_path, settings.auto_battery_soc_path)
        configured = EnergySourceDefinition(source_id="configured", service_name="svc", soc_path="/Soc")
        configured_catalog = EnergySourceCatalog(replace(settings, auto_energy_sources=(configured,)), gateway)
        self.assertIs(configured_catalog.primary_source(), configured)
        self.assertEqual(configured_catalog.primary_service_prefix(), settings.auto_battery_service_prefix)

    def test_catalog_checks_readability_through_gateway(self) -> None:
        gateway = FakeGateway()
        source = EnergySourceDefinition(source_id="battery", service_name="svc", soc_path="/Soc")
        catalog = EnergySourceCatalog(replace(helper_settings(), auto_energy_sources=(source,)), gateway)
        self.assertFalse(catalog.source_has_readable_data(source, "svc"))
        gateway.values[("svc", "/Soc")] = 50.0
        self.assertTrue(catalog.source_has_readable_data(source, "svc"))
        self.assertTrue(catalog.battery_service_has_soc("svc"))
        with patch.object(gateway, "cached_value", side_effect=OSError("gateway")):
            self.assertFalse(catalog.battery_service_has_soc("svc"))
            self.assertFalse(catalog.source_has_readable_data(source, "svc"))

    def test_catalog_ignores_empty_paths_and_non_positive_capacity(self) -> None:
        gateway = FakeGateway()
        settings = replace(helper_settings(), auto_energy_sources=(), auto_battery_capacity_wh=0.0)
        catalog = EnergySourceCatalog(settings, gateway)
        source = EnergySourceDefinition(source_id="empty")
        self.assertIsNone(catalog.primary_source().usable_capacity_wh)
        self.assertFalse(catalog.source_has_readable_data(source, "svc"))

    def test_resolver_prefers_readable_configured_service(self) -> None:
        source = EnergySourceDefinition(
            source_id="battery",
            service_name="svc.configured",
            service_prefix="svc.",
            soc_path="/Soc",
        )
        gateway = FakeGateway()
        gateway.services = ["svc.configured"]
        catalog = FakeCatalog(source)
        catalog.readable.add(("battery", "svc.configured"))
        resolver = EnergyServiceResolver(helper_settings(), gateway, catalog)
        self.assertEqual(resolver.resolve(source), "svc.configured")
        gateway.services = []
        self.assertEqual(resolver.resolve(source), "svc.configured")

    def test_resolver_discovers_and_invalidates_primary(self) -> None:
        source = EnergySourceDefinition(source_id="battery", service_prefix="svc.", soc_path="/Soc")
        gateway = FakeGateway()
        gateway.services = ["svc.discovered"]
        catalog = FakeCatalog(source)
        catalog.readable.add(("battery", "svc.discovered"))
        resolver = EnergyServiceResolver(helper_settings(), gateway, catalog)
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=100.0):
            self.assertEqual(resolver.resolve(source), "svc.discovered")
        resolver.invalidate_primary()
        self.assertIsNone(resolver._resolved_primary)

    def test_resolver_reports_missing_service(self) -> None:
        source = EnergySourceDefinition(source_id="battery", service_prefix="missing.", soc_path="/Soc")
        resolver = EnergyServiceResolver(helper_settings(), FakeGateway(), FakeCatalog(source))
        with self.assertRaisesRegex(ValueError, "No DBus service found"):
            resolver.resolve(source)

    def test_resolver_rejects_unreadable_configured_services(self) -> None:
        source = EnergySourceDefinition(source_id="battery", service_name="configured", service_prefix="missing.")
        gateway = FakeGateway()
        gateway.services = ["configured"]
        catalog = FakeCatalog(source)
        resolver = EnergyServiceResolver(helper_settings(), gateway, catalog)
        with self.assertRaisesRegex(ValueError, "No DBus service found"):
            resolver.resolve(source)
        secondary = EnergySourceDefinition(source_id="secondary", service_name="configured", service_prefix="other.")
        with self.assertRaisesRegex(ValueError, "No DBus service found"):
            resolver.resolve(secondary)

    def test_resolver_contains_expected_catalog_read_errors(self) -> None:
        source = EnergySourceDefinition(source_id="battery", service_name="configured", service_prefix="missing.")
        gateway = FakeGateway()
        gateway.services = ["configured"]
        catalog = FakeCatalog(source)
        resolver = EnergyServiceResolver(helper_settings(), gateway, catalog)
        with patch.object(catalog, "source_has_readable_data", side_effect=OSError("cache")):
            with self.assertRaisesRegex(ValueError, "No DBus service found"):
                resolver.resolve(source)

    def test_non_primary_discovery_reports_no_matching_candidate(self) -> None:
        primary = EnergySourceDefinition(source_id="primary", service_prefix="primary.")
        secondary = EnergySourceDefinition(source_id="secondary", service_prefix="secondary.")
        resolver = EnergyServiceResolver(helper_settings(), FakeGateway(), FakeCatalog(primary))
        with self.assertRaisesRegex(ValueError, "No DBus service found for energy source"):
            resolver.resolve(secondary)

    def test_non_primary_resolver_uses_config_cache_and_discovery(self) -> None:
        primary = EnergySourceDefinition(source_id="primary", service_prefix="primary.", soc_path="/Soc")
        secondary = EnergySourceDefinition(
            source_id="secondary", service_name="secondary.config", service_prefix="secondary.", soc_path="/Soc"
        )
        gateway = FakeGateway()
        gateway.services = ["secondary.config"]
        catalog = FakeCatalog(primary)
        catalog.readable.add(("secondary", "secondary.config"))
        resolver = EnergyServiceResolver(helper_settings(), gateway, catalog)
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", side_effect=[10.0, 11.0]):
            self.assertEqual(resolver.resolve(secondary), "secondary.config")
            gateway.services = []
            self.assertEqual(resolver.resolve(secondary), "secondary.config")
        discovered = EnergySourceDefinition(source_id="other", service_prefix="other.", soc_path="/Soc")
        gateway.services = ["other.found"]
        catalog.readable.add(("other", "other.found"))
        self.assertEqual(resolver.resolve(discovered), "other.found")

    def test_non_primary_without_prefix_reports_configuration_error(self) -> None:
        primary = EnergySourceDefinition(source_id="primary", service_prefix="primary.", soc_path="/Soc")
        source = EnergySourceDefinition(source_id="secondary")
        resolver = EnergyServiceResolver(helper_settings(), FakeGateway(), FakeCatalog(primary))
        with self.assertRaisesRegex(ValueError, "No readable DBus service configured"):
            resolver.resolve(source)


if __name__ == "__main__":
    unittest.main()
