# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import MagicMock, call, patch

from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.inputs.helper.sources_dbus_resolve import (
    _AutoInputHelperSourceDbusResolve,
    _required_service_name,
    _service_name_or_none,
)
from venus_evcharger.inputs.helper.sources_dbus_snapshot import (
    _AutoInputHelperSourceDbusSnapshot,
    _capacity_payload_float,
    _capacity_payload_int,
    _capacity_payload_mapping,
)


class TestAutoInputSourcesResolutionSnapshotContracts(unittest.TestCase):
    @staticmethod
    def _owner(role: type[_AutoInputHelperSourceDbusResolve] = _AutoInputHelperSourceDbusSnapshot, **values: object):
        owner = object.__new__(role)
        for key, value in values.items():
            setattr(owner, key, value)
        return owner

    def test_service_name_contracts_strip_and_require_values(self) -> None:
        self.assertEqual(_service_name_or_none(" service "), "service")
        self.assertIsNone(_service_name_or_none(None))
        self.assertIsNone(_service_name_or_none("  "))
        self.assertEqual(_service_name_or_none(42), "42")
        self.assertEqual(_required_service_name(" service ", "battery"), "service")
        with self.assertRaisesRegex(ValueError, "No DBus service resolved for battery"):
            _required_service_name("", "battery")

    def test_service_cache_initializes_and_updates_primary_state_exactly(self) -> None:
        owner = self._owner(
            _resolved_auto_energy_services=None,
            _auto_energy_last_scan=None,
            _resolved_auto_battery_service=None,
            _auto_battery_last_scan=0.0,
        )
        owner._cache_energy_service("source", "service", 12.5, primary=True)
        self.assertEqual(owner._resolved_auto_energy_services, {"source": "service"})
        self.assertEqual(owner._auto_energy_last_scan, {"source": 12.5})
        self.assertEqual(owner._resolved_auto_battery_service, "service")
        self.assertEqual(owner._auto_battery_last_scan, 12.5)

        owner._cache_energy_service("secondary", "other", 13.0, primary=False)
        self.assertEqual(owner._resolved_auto_energy_services["secondary"], "other")
        self.assertEqual(owner._auto_energy_last_scan["secondary"], 13.0)
        self.assertEqual(owner._resolved_auto_battery_service, "service")

        owner._cache_energy_service("implicit-secondary", "third", 14.0)
        self.assertEqual(owner._resolved_auto_battery_service, "service")

    def test_configured_and_discovered_service_resolution_contracts(self) -> None:
        source = EnergySourceDefinition("source", service_name="configured", service_prefix="prefix")
        owner = self._owner()
        owner._dbus_service_name_available = MagicMock(return_value=True)
        owner._energy_source_has_readable_data = MagicMock(return_value=True)
        owner._cache_energy_service = MagicMock()
        self.assertEqual(owner._configured_energy_source_service(source, 10.0), "configured")
        owner._dbus_service_name_available.assert_called_once_with("configured")
        owner._energy_source_has_readable_data.assert_called_once_with(source, "configured")
        owner._cache_energy_service.assert_called_once_with("source", "configured", 10.0)

        owner._list_dbus_services = MagicMock(return_value=["prefix.2", "prefix.1"])
        owner._energy_source_has_readable_data = MagicMock(return_value=True)
        owner._cache_energy_service.reset_mock()
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_resolve.first_matching_prefixed_service",
            return_value="prefix.1",
        ) as first_match:
            self.assertEqual(owner._discovered_energy_source_service(source, 11.0), "prefix.1")
        self.assertEqual(first_match.call_args.args[:2], (["prefix.2", "prefix.1"], "prefix"))
        self.assertTrue(first_match.call_args.args[2]("candidate"))
        owner._energy_source_has_readable_data.assert_called_with(source, "candidate")
        owner._cache_energy_service.assert_called_once_with("source", "prefix.1", 11.0)

    def test_resolve_energy_source_uses_configured_cached_then_discovered_precedence(self) -> None:
        source = EnergySourceDefinition("secondary")
        primary = EnergySourceDefinition("primary")
        owner = self._owner()
        owner._primary_energy_source = MagicMock(return_value=primary)
        owner._configured_energy_source_service = MagicMock(return_value=" configured ")
        owner._cached_energy_service = MagicMock(return_value="cached")
        owner._discovered_energy_source_service = MagicMock(return_value="discovered")
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=50.0):
            self.assertEqual(owner._resolve_energy_source_service(source), "configured")
        owner._configured_energy_source_service.assert_called_once_with(source, 50.0)
        owner._cached_energy_service.assert_not_called()
        owner._discovered_energy_source_service.assert_not_called()

        owner._configured_energy_source_service.return_value = None
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=51.0):
            self.assertEqual(owner._resolve_energy_source_service(source), "cached")
        owner._cached_energy_service.assert_called_once_with("secondary", 51.0)

        owner._cached_energy_service.return_value = None
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=52.0):
            self.assertEqual(owner._resolve_energy_source_service(source), "discovered")
        owner._discovered_energy_source_service.assert_called_once_with(source, 52.0)

        owner._resolve_auto_battery_service = MagicMock(return_value="primary-service")
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=53.0):
            self.assertEqual(owner._resolve_energy_source_service(primary), "primary-service")
        owner._resolve_auto_battery_service.assert_called_once_with()

    def test_auto_battery_resolution_precedence_and_discovery_calls_are_exact(self) -> None:
        source = EnergySourceDefinition("primary", service_prefix="source.prefix")
        owner = self._owner(auto_battery_scan_interval_seconds=60.0)
        owner._configured_auto_battery_service = MagicMock(return_value=" configured ")
        owner._cached_auto_battery_service = MagicMock(return_value="cached")
        owner._discovered_auto_battery_service = MagicMock(return_value="discovered")
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=10.0):
            self.assertEqual(owner._resolve_auto_battery_service(), "configured")
        owner._configured_auto_battery_service.assert_called_once_with(10.0)
        owner._cached_auto_battery_service.assert_not_called()

        owner._configured_auto_battery_service.return_value = None
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=11.0):
            self.assertEqual(owner._resolve_auto_battery_service(), "cached")
        owner._cached_auto_battery_service.assert_called_once_with(11.0)

        owner._cached_auto_battery_service.return_value = None
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=12.0):
            self.assertEqual(owner._resolve_auto_battery_service(), "discovered")
        owner._discovered_auto_battery_service.assert_called_once_with(12.0)

        discovery_owner = self._owner()
        discovery_owner._primary_energy_source = MagicMock(return_value=source)
        discovery_owner._primary_energy_service_prefix = MagicMock(return_value="fallback.prefix")
        discovery_owner._list_dbus_services = MagicMock(return_value=["source.prefix.1"])
        discovery_owner._battery_service_has_soc = MagicMock(return_value=True)
        discovery_owner._cache_energy_service = MagicMock()
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_resolve.first_matching_prefixed_service",
            return_value="source.prefix.1",
        ) as first_match:
            self.assertEqual(discovery_owner._discovered_auto_battery_service(12.0), "source.prefix.1")
        first_match.assert_called_once_with(
            ["source.prefix.1"], "source.prefix", discovery_owner._battery_service_has_soc
        )
        discovery_owner._cache_energy_service.assert_called_once_with(
            "primary", "source.prefix.1", 12.0, primary=True
        )

        discovery_owner._primary_energy_source.return_value = EnergySourceDefinition("primary")
        discovery_owner._primary_energy_service_prefix.return_value = "fallback.prefix"
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_resolve.first_matching_prefixed_service",
            return_value=None,
        ):
            with self.assertRaisesRegex(ValueError, "fallback.prefix"):
                discovery_owner._discovered_auto_battery_service(13.0)

    def test_configured_primary_service_and_generic_cache_contracts(self) -> None:
        source = EnergySourceDefinition("primary", service_name="configured")
        owner = self._owner(
            _resolved_auto_battery_service="cached",
            _auto_battery_last_scan=10.0,
            auto_battery_scan_interval_seconds=60.0,
            _resolved_auto_energy_services={"secondary": " service "},
            _auto_energy_last_scan={"secondary": 20.0},
        )
        owner._primary_energy_source = MagicMock(return_value=source)
        owner._dbus_service_name_available = MagicMock(return_value=True)
        owner._energy_source_has_readable_data = MagicMock(return_value=True)
        self.assertEqual(owner._configured_auto_battery_service(30.0), "configured")
        owner._energy_source_has_readable_data.assert_called_once_with(source, "configured")
        self.assertEqual(owner._resolved_auto_battery_service, "configured")
        self.assertEqual(owner._auto_battery_last_scan, 30.0)
        self.assertEqual(
            owner._resolved_auto_energy_services,
            {"secondary": " service ", "primary": "configured"},
        )

        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.discovery_cache_valid", return_value=True) as valid:
            self.assertEqual(owner._cached_energy_service("secondary", 25.0), "service")
        valid.assert_called_once_with(" service ", 20.0, 60.0, 25.0)

        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.discovery_cache_valid", return_value=False):
            self.assertIsNone(owner._cached_energy_service("secondary", 26.0))
        owner._resolved_auto_energy_services = None
        owner._auto_energy_last_scan = None
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.discovery_cache_valid", return_value=False) as valid:
            self.assertIsNone(owner._cached_energy_service("missing", 27.0))
        valid.assert_called_once_with(None, 0.0, 60.0, 27.0)

        owner._resolved_auto_energy_services = {}
        owner._auto_energy_last_scan = {}
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.discovery_cache_valid", return_value=False) as valid:
            self.assertIsNone(owner._cached_energy_service("missing", 28.0))
        valid.assert_called_once_with(None, 0.0, 60.0, 28.0)

        owner = self._owner()
        owner._primary_energy_source = MagicMock(return_value=EnergySourceDefinition("primary", service_name="configured"))
        owner._dbus_service_name_available = MagicMock(return_value=False)
        owner._energy_source_has_readable_data = MagicMock()
        self.assertIsNone(owner._configured_auto_battery_service(1.0))
        owner._energy_source_has_readable_data.assert_not_called()

    def test_required_resolution_labels_are_preserved_on_blank_results(self) -> None:
        owner = self._owner()
        owner._configured_auto_battery_service = MagicMock(return_value=None)
        owner._cached_auto_battery_service = MagicMock(return_value=None)
        owner._discovered_auto_battery_service = MagicMock(return_value="")
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.time.time", return_value=1.0):
            with self.assertRaises(ValueError) as raised:
                owner._resolve_auto_battery_service()
        self.assertEqual(str(raised.exception), "No DBus service resolved for auto battery")

        primary = EnergySourceDefinition("primary")
        owner._primary_energy_source = MagicMock(return_value=primary)
        owner._resolve_auto_battery_service = MagicMock(return_value="")
        with self.assertRaises(ValueError) as raised:
            owner._resolve_energy_source_service(primary)
        self.assertEqual(str(raised.exception), "No DBus service resolved for primary energy source")

        secondary = EnergySourceDefinition("secondary")
        owner._configured_energy_source_service = MagicMock(return_value=None)
        owner._cached_energy_service = MagicMock(return_value=None)
        owner._discovered_energy_source_service = MagicMock(return_value="")
        with self.assertRaisesRegex(ValueError, "secondary"):
            owner._resolve_energy_source_service(secondary)

        missing_cache_owner = self._owner(auto_battery_scan_interval_seconds=60.0)
        with patch("venus_evcharger.inputs.helper.sources_dbus_resolve.discovery_cache_valid", return_value=False) as valid:
            self.assertIsNone(missing_cache_owner._cached_energy_service("missing", 2.0))
        valid.assert_called_once_with(None, 0.0, 60.0, 2.0)

    def test_optional_energy_reads_use_exact_service_path_and_normalization(self) -> None:
        owner = self._owner()
        owner._get_dbus_value = MagicMock(side_effect=[12, True, " mode ", None])
        self.assertEqual(owner._read_optional_energy_value("service", "/Power"), 12.0)
        self.assertIsNone(owner._read_optional_energy_value("service", "/Boolean"))
        self.assertEqual(owner._read_optional_energy_text("service", "/Mode"), "mode")
        self.assertEqual(owner._read_optional_energy_text("service", "/Missing"), "")
        self.assertEqual(
            owner._get_dbus_value.call_args_list,
            [
                call("service", "/Power"),
                call("service", "/Boolean"),
                call("service", "/Mode"),
                call("service", "/Missing"),
            ],
        )
        self.assertIsNone(owner._read_optional_energy_value("service", ""))
        self.assertEqual(owner._read_optional_energy_text("service", ""), "")

    def test_capacity_payload_boundary_helpers_are_typed(self) -> None:
        self.assertEqual(_capacity_payload_mapping({1: "2"}), {"1": "2"})
        self.assertIsNone(_capacity_payload_mapping([]))
        self.assertEqual(_capacity_payload_float({"value": 2.5}, "value"), 2.5)
        self.assertIsNone(_capacity_payload_float({"value": True}, "value"))
        self.assertEqual(_capacity_payload_int({"value": 16}, "value"), 16)

    def test_read_energy_fields_preserves_field_order_and_arguments(self) -> None:
        source = EnergySourceDefinition(
            "source",
            soc_path="/Soc",
            battery_power_path="/Battery",
            ac_power_path="/Ac",
            pv_power_path="/Pv",
            grid_interaction_path="/Grid",
            operating_mode_path="/Mode",
        )
        owner = self._owner()
        owner._read_optional_energy_value = MagicMock(side_effect=[1, 2, 3, 4, 5])
        owner._read_optional_energy_text = MagicMock(return_value="mode")
        self.assertEqual(owner._read_dbus_energy_source_fields(source, "service"), (1, 2, 3, 4, 5, "mode"))
        self.assertEqual(
            owner._read_optional_energy_value.call_args_list,
            [
                call("service", "/Soc"),
                call("service", "/Battery"),
                call("service", "/Ac"),
                call("service", "/Pv"),
                call("service", "/Grid"),
            ],
        )
        owner._read_optional_energy_text.assert_called_once_with("service", "/Mode")

    def test_soc_validation_accepts_boundaries_and_reports_invalid_values_exactly(self) -> None:
        source = EnergySourceDefinition("source", soc_path="/Soc")
        owner = self._owner(auto_battery_scan_interval_seconds=2.0)
        owner._warning_throttled = MagicMock()
        owner._delay_source_retry = MagicMock()
        for value in (None, 0.0, 100.0, 50.0):
            self.assertEqual(owner._validated_energy_source_soc(source, "service", value), value)
        self.assertIsNone(owner._validated_energy_source_soc(source, "service", 100.001))
        owner._warning_throttled.assert_called_once_with(
            "auto-helper-battery-soc-invalid",
            5.0,
            "Auto input helper ignored out-of-range battery SOC %s from %s %s",
            100.001,
            "service",
            "/Soc",
        )
        owner._delay_source_retry.assert_called_once_with("battery")

    def test_snapshot_payload_maps_every_field_exactly(self) -> None:
        source = EnergySourceDefinition("source", role="inverter", battery_chemistry="nmc")
        owner = self._owner()
        owner._dbus_energy_source_capacity_payload = MagicMock(
            return_value={
                "usable_capacity_wh": 5000.0,
                "usable_capacity_source": "configured",
                "installed_capacity_ah": 100.0,
                "capacity_voltage_v": 50.0,
                "capacity_nominal_voltage_v": 48.0,
                "capacity_cell_count": 15,
            }
        )
        snapshot = owner._dbus_energy_source_snapshot_payload(
            source, "service", 55.0, -200.0, 300.0, 400.0, -50.0, "support", 123.0
        )
        self.assertEqual(
            snapshot,
            EnergySourceSnapshot(
                source_id="source",
                role="inverter",
                service_name="service",
                soc=55.0,
                usable_capacity_wh=5000.0,
                usable_capacity_source="configured",
                installed_capacity_ah=100.0,
                capacity_voltage_v=50.0,
                capacity_nominal_voltage_v=48.0,
                capacity_cell_count=15,
                battery_chemistry="nmc",
                net_battery_power_w=-200.0,
                ac_power_w=300.0,
                pv_input_power_w=400.0,
                grid_interaction_w=-50.0,
                operating_mode="support",
                online=True,
                confidence=1.0,
                captured_at=123.0,
            ),
        )
        owner._dbus_energy_source_capacity_payload.assert_called_once_with(source, "service", 55.0, 123.0)

    def test_capacity_inference_boundaries_and_voltage_signature_are_exact(self) -> None:
        lfp = EnergySourceDefinition("source", capacity_auto_estimate=True, battery_chemistry="lfp", capacity_estimate_min_soc=95)
        self.assertFalse(_AutoInputHelperSourceDbusSnapshot._dbus_capacity_inference_allowed(lfp, 94.999))
        self.assertTrue(_AutoInputHelperSourceDbusSnapshot._dbus_capacity_inference_allowed(lfp, 95.0))
        self.assertEqual(_AutoInputHelperSourceDbusSnapshot._lfp_nominal_voltage_from_full_voltage(40.0), (48.0, 15))
        self.assertEqual(_AutoInputHelperSourceDbusSnapshot._lfp_nominal_voltage_from_full_voltage(52.499), (48.0, 15))
        self.assertEqual(_AutoInputHelperSourceDbusSnapshot._lfp_nominal_voltage_from_full_voltage(52.5), (51.2, 16))
        self.assertEqual(_AutoInputHelperSourceDbusSnapshot._lfp_nominal_voltage_from_full_voltage(60.0), (51.2, 16))
        for voltage in (None, 39.999, 60.001):
            self.assertEqual(_AutoInputHelperSourceDbusSnapshot._lfp_nominal_voltage_from_full_voltage(voltage), (None, None))

        owner = self._owner()
        owner._read_optional_energy_value = MagicMock(side_effect=[0.0, -1.0, 0.001])
        self.assertIsNone(owner._read_positive_optional_energy_value("service", "/Zero"))
        self.assertIsNone(owner._read_positive_optional_energy_value("service", "/Negative"))
        self.assertEqual(owner._read_positive_optional_energy_value("service", "/Positive"), 0.001)

    def test_primary_retry_and_snapshot_orchestration_are_exact(self) -> None:
        primary = EnergySourceDefinition("primary")
        owner = self._owner()
        owner._primary_energy_source = MagicMock(return_value=primary)
        owner._resolve_energy_source_service = MagicMock(side_effect=["first", "second"])
        owner._read_dbus_energy_source_fields = MagicMock(
            side_effect=[RuntimeError("offline"), (1.0, 2.0, 3.0, 4.0, 5.0, "mode")]
        )
        owner._invalidate_auto_battery_service = MagicMock()
        self.assertEqual(
            owner._read_dbus_energy_source_fields_with_primary_retry(primary),
            ("second", (1.0, 2.0, 3.0, 4.0, 5.0, "mode")),
        )
        self.assertEqual(owner._resolve_energy_source_service.call_args_list, [call(primary), call(primary)])
        owner._invalidate_auto_battery_service.assert_called_once_with()
        self.assertEqual(
            owner._read_dbus_energy_source_fields.call_args_list,
            [call(primary, "first"), call(primary, "second")],
        )

        owner = self._owner()
        owner._read_dbus_energy_source_fields_with_primary_retry = MagicMock(
            return_value=("service", (55.0, -2.0, 3.0, 4.0, 5.0, "mode"))
        )
        owner._validated_energy_source_soc = MagicMock(return_value=54.0)
        with patch.object(
            _AutoInputHelperSourceDbusSnapshot,
            "_dbus_energy_source_snapshot_payload",
            return_value=EnergySourceSnapshot("result", "battery", "s"),
        ) as payload:
            result = owner._dbus_energy_source_snapshot(primary, 100.0)
        self.assertEqual(result.source_id, "result")
        owner._validated_energy_source_soc.assert_called_once_with(primary, "service", 55.0)
        payload.assert_called_once_with(
            owner, primary, "service", 54.0, -2.0, 3.0, 4.0, 5.0, "mode", 100.0
        )

    def test_capacity_resolution_and_persistence_state_transitions_are_exact(self) -> None:
        source = EnergySourceDefinition("source", capacity_startup_recheck_seconds=10.0)
        self.assertEqual(
            _AutoInputHelperSourceDbusSnapshot._resolved_dbus_capacity_payload(None, None),
            {"usable_capacity_wh": None, "usable_capacity_source": ""},
        )
        self.assertEqual(
            _AutoInputHelperSourceDbusSnapshot._resolved_dbus_capacity_payload(
                {"usable_capacity_wh": 1}, {"usable_capacity_wh": 2}
            ),
            {"usable_capacity_wh": 1},
        )

        owner = self._owner(config_path="config.ini")
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_snapshot.persist_estimated_capacity_if_ah_changed",
            return_value=True,
        ) as persist, patch("venus_evcharger.inputs.helper.sources_dbus_snapshot.logging.info") as info:
            owner._persist_dbus_capacity_payload_if_needed(source, {"installed_capacity_ah": 100}, True)
        persist.assert_called_once_with("config.ini", source, {"installed_capacity_ah": 100})
        info.assert_called_once_with("Persisted auto-estimated battery capacity for source %s", "source")

        owner = self._owner(_auto_battery_capacity_startup_recheck_at=10.0)
        self.assertFalse(owner._dbus_capacity_startup_recheck_time_due(source, 9.999))
        self.assertTrue(owner._dbus_capacity_startup_recheck_time_due(source, 10.0))
        owner._auto_battery_capacity_startup_recheck_at = 0.0
        self.assertFalse(owner._dbus_capacity_startup_recheck_time_due(source, 100.0))
        missing_recheck = self._owner()
        self.assertFalse(missing_recheck._dbus_capacity_startup_recheck_time_due(source, 100.0))
        zero_duration = EnergySourceDefinition("source", capacity_startup_recheck_seconds=0.0)
        missing_recheck._auto_battery_capacity_startup_recheck_at = 1.0
        self.assertFalse(missing_recheck._dbus_capacity_startup_recheck_time_due(zero_duration, 1.0))
        self.assertFalse(missing_recheck._dbus_capacity_startup_recheck_seen(source, "service"))

        owner = self._owner(config_path="config.ini")
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_snapshot.persist_estimated_capacity_if_ah_changed",
            return_value=False,
        ) as persist, patch("venus_evcharger.inputs.helper.sources_dbus_snapshot.logging.info") as info:
            owner._persist_dbus_capacity_payload_if_needed(source, {}, False)
            persist.assert_not_called()
            owner._persist_dbus_capacity_payload_if_needed(source, {}, True)
        persist.assert_called_once_with("config.ini", source, {})
        info.assert_not_called()

        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_snapshot.persist_estimated_capacity_if_ah_changed",
            side_effect=ValueError("bad capacity"),
        ), patch("venus_evcharger.inputs.helper.sources_dbus_snapshot.logging.warning") as warning:
            owner._persist_dbus_capacity_payload_if_needed(source, {}, True)
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[0], "Unable to persist auto-estimated battery capacity: %s")
        self.assertEqual(str(warning.call_args.args[1]), "bad capacity")

        no_path_owner = self._owner()
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_snapshot.persist_estimated_capacity_if_ah_changed",
            return_value=False,
        ) as persist:
            no_path_owner._persist_dbus_capacity_payload_if_needed(source, {}, True)
        persist.assert_called_once_with("", source, {})

    def test_capacity_payload_selection_and_inference_calls_are_exact(self) -> None:
        configured = EnergySourceDefinition("source", usable_capacity_wh=0.001)
        self.assertEqual(
            _AutoInputHelperSourceDbusSnapshot._configured_dbus_capacity_payload(configured),
            {"usable_capacity_wh": 0.001, "usable_capacity_source": "configured"},
        )
        self.assertIsNone(
            _AutoInputHelperSourceDbusSnapshot._configured_dbus_capacity_payload(
                EnergySourceDefinition("source", usable_capacity_wh=0.0)
            )
        )

        source = EnergySourceDefinition(
            "source",
            capacity_wh_path="/Wh",
            capacity_ah_path="/Ah",
            voltage_path="/Voltage",
            capacity_estimate_min_soc=95.0,
        )
        owner = self._owner()
        owner._read_positive_optional_energy_value = MagicMock(side_effect=[None, 100.0, 51.2])
        with patch.object(
            _AutoInputHelperSourceDbusSnapshot,
            "_lfp_inferred_dbus_capacity_payload",
            return_value={"usable_capacity_wh": 5120.0},
        ) as inferred:
            self.assertEqual(owner._infer_dbus_capacity_payload(source, "service", 95.0), {"usable_capacity_wh": 5120.0})
        self.assertEqual(
            owner._read_positive_optional_energy_value.call_args_list,
            [call("service", "/Wh"), call("service", "/Ah"), call("service", "/Voltage")],
        )
        inferred.assert_called_once_with(owner, 100.0, 51.2)

        owner = self._owner()
        owner._lfp_nominal_voltage_from_full_voltage = MagicMock(return_value=(51.2, 16))
        self.assertEqual(
            owner._lfp_inferred_dbus_capacity_payload(100.0, 53.0),
            {
                "usable_capacity_wh": 5120.0,
                "usable_capacity_source": "dbus_lfp_inferred",
                "installed_capacity_ah": 100.0,
                "capacity_voltage_v": 53.0,
                "capacity_nominal_voltage_v": 51.2,
                "capacity_cell_count": 16,
            },
        )
        owner._lfp_nominal_voltage_from_full_voltage.assert_called_once_with(53.0)

        for installed_ah, signature in ((None, (48.0, 15)), (100.0, (None, 15)), (100.0, (48.0, None))):
            owner = self._owner()
            owner._lfp_nominal_voltage_from_full_voltage = MagicMock(return_value=signature)
            self.assertIsNone(owner._lfp_inferred_dbus_capacity_payload(installed_ah, 50.0))

    def test_snapshot_missing_capacity_source_and_fresh_capacity_flow_are_exact(self) -> None:
        source = EnergySourceDefinition("source")
        owner = self._owner()
        owner._dbus_energy_source_capacity_payload = MagicMock(return_value={})
        snapshot = owner._dbus_energy_source_snapshot_payload(
            source, "service", None, None, None, None, None, "", 1.0
        )
        self.assertEqual(snapshot.usable_capacity_source, "")

        owner = self._owner()
        owner._store_dbus_capacity_payload = MagicMock()
        owner._persist_dbus_capacity_payload_if_needed = MagicMock()
        with patch.object(
            _AutoInputHelperSourceDbusSnapshot,
            "_infer_dbus_capacity_payload",
            return_value={"usable_capacity_wh": 1.0},
        ) as infer:
            payload = owner._fresh_dbus_capacity_payload(source, "service", 99.0, True)
        self.assertEqual(payload, {"usable_capacity_wh": 1.0})
        infer.assert_called_once_with(owner, source, "service", 99.0)
        owner._store_dbus_capacity_payload.assert_called_once_with(
            source, "service", payload, startup_recheck_done=True
        )
        owner._persist_dbus_capacity_payload_if_needed.assert_called_once_with(source, payload, True)

        owner = self._owner()
        owner._read_optional_energy_value = MagicMock(return_value=1.0)
        self.assertEqual(owner._read_positive_optional_energy_value("service", "/Value"), 1.0)
        owner._read_optional_energy_value.assert_called_once_with("service", "/Value")

    def test_soc_warning_interval_uses_scan_interval_above_floor(self) -> None:
        source = EnergySourceDefinition("source")
        owner = self._owner(auto_battery_scan_interval_seconds=10.0)
        owner._warning_throttled = MagicMock()
        owner._delay_source_retry = MagicMock()
        self.assertIsNone(owner._validated_energy_source_soc(source, "service", -0.001))
        self.assertEqual(owner._warning_throttled.call_args.args[1], 10.0)

        owner = self._owner(auto_battery_scan_interval_seconds=0.0)
        owner._warning_throttled = MagicMock()
        owner._delay_source_retry = MagicMock()
        owner._validated_energy_source_soc(source, "service", -1.0)
        self.assertEqual(owner._warning_throttled.call_args.args[1], 5.0)

    def test_capacity_recheck_falls_back_to_cached_payload_when_refresh_fails(self) -> None:
        source = EnergySourceDefinition("source", estimated_capacity_wh=4800.0)
        owner = self._owner()
        self.assertEqual(
            owner._cached_dbus_capacity_payload(source, "service"),
            {"usable_capacity_wh": 4800.0, "usable_capacity_source": "config_estimated"},
        )

        cached = {"usable_capacity_wh": 4000.0, "usable_capacity_source": "cached"}
        with (
            patch.object(_AutoInputHelperSourceDbusSnapshot, "_configured_dbus_capacity_payload", return_value=None),
            patch.object(_AutoInputHelperSourceDbusSnapshot, "_cached_dbus_capacity_payload", return_value=cached),
            patch.object(_AutoInputHelperSourceDbusSnapshot, "_dbus_capacity_startup_recheck_due", return_value=True),
            patch.object(_AutoInputHelperSourceDbusSnapshot, "_fresh_dbus_capacity_payload", return_value=None),
        ):
            self.assertIs(owner._dbus_energy_source_capacity_payload(source, "service", 99.0, 10.0), cached)

        half_second = self._owner(_auto_battery_capacity_startup_recheck_at=0.5)
        active = EnergySourceDefinition("source", capacity_startup_recheck_seconds=1.0)
        self.assertTrue(half_second._dbus_capacity_startup_recheck_time_due(active, 0.5))


if __name__ == "__main__":
    unittest.main()
