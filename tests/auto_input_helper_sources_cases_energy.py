# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_sources_cases_common import *


class _AutoInputHelperSourcesEnergyCases:
    def test_dynamic_energy_source_resolution_and_battery_snapshot_cover_new_paths(self):
        helper = self._make_helper()
        helper.auto_energy_sources = (
            EnergySourceDefinition(
                source_id="primary_battery",
                role="battery",
                connector_type="dbus",
                service_name="configured-primary",
                service_prefix="com.victronenergy.battery",
                soc_path="/Soc",
            ),
            EnergySourceDefinition(
                source_id="hybrid",
                role="hybrid-inverter",
                connector_type="dbus",
                service_name="configured-hybrid",
                service_prefix="com.victronenergy.hybrid",
                soc_path="/Soc",
                battery_power_path="/Dc/0/Power",
            ),
        )
        helper._energy_source_has_readable_data = MagicMock(side_effect=[True, True])
        helper._list_dbus_services = MagicMock(return_value=["configured-hybrid"])
        helper._resolve_auto_battery_service = MagicMock(return_value="resolved-primary")

        self.assertEqual(helper._resolve_energy_source_service(helper.auto_energy_sources[0]), "resolved-primary")
        self.assertEqual(helper._resolve_energy_source_service(helper.auto_energy_sources[1]), "configured-hybrid")

        helper._resolved_auto_energy_services = {"hybrid": "cached-hybrid"}
        helper._auto_energy_last_scan = {"hybrid": 100.0}
        cached_source = EnergySourceDefinition(
            source_id="hybrid",
            role="hybrid-inverter",
            connector_type="dbus",
            service_prefix="com.victronenergy.hybrid",
            soc_path="/Soc",
            battery_power_path="/Dc/0/Power",
        )
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=120.0):
            self.assertEqual(helper._resolve_energy_source_service(cached_source), "cached-hybrid")

        helper = self._make_helper()
        helper.auto_energy_sources = (EnergySourceDefinition(source_id="primary_battery", role="battery", connector_type="dbus"),)
        with patch(
            "venus_evcharger.inputs.helper.sources.read_energy_source_snapshot",
            return_value=EnergySourceSnapshot(
                source_id="primary_battery",
                role="battery",
                service_name="svc",
                soc=55.0,
                usable_capacity_wh=5000.0,
                net_battery_power_w=-500.0,
                grid_interaction_w=-100.0,
                online=True,
                confidence=0.8,
                captured_at=100.0,
            ),
        ), patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            snapshot = helper._get_battery_snapshot()

        self.assertEqual(snapshot["battery_soc"], 55.0)
        self.assertEqual(snapshot["battery_combined_soc"], 55.0)
        self.assertEqual(snapshot["battery_headroom_charge_w"], 0.0)
        self.assertEqual(snapshot["expected_near_term_export_w"], 475.0)

        helper = self._make_helper()
        helper.auto_energy_sources = (
            EnergySourceDefinition(source_id="victron", profile_name="dbus-battery", role="battery", connector_type="dbus"),
            EnergySourceDefinition(
                source_id="huawei",
                profile_name="huawei_ma_native_ap",
                role="hybrid-inverter",
                connector_type="dbus",
            ),
        )
        helper._energy_learning_profiles = {
            "victron": EnergyLearningProfile(source_id="victron", observed_min_discharge_soc=40.0),
            "huawei": EnergyLearningProfile(source_id="huawei", observed_min_discharge_soc=20.0),
        }
        with patch(
            "venus_evcharger.inputs.helper.sources.read_energy_source_snapshot",
            side_effect=[
                EnergySourceSnapshot(
                    source_id="victron",
                    role="battery",
                    service_name="svc-victron",
                    soc=60.0,
                    usable_capacity_wh=10000.0,
                    net_battery_power_w=1500.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="huawei",
                    role="hybrid-inverter",
                    service_name="svc-huawei",
                    soc=60.0,
                    usable_capacity_wh=5000.0,
                    net_battery_power_w=0.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            ],
        ), patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            snapshot = helper._get_battery_snapshot()

        battery_sources = cast(list[dict[str, Any]], snapshot["battery_sources"])
        self.assertEqual(snapshot["battery_discharge_balance_mode"], "capacity_reserve_weighted")
        self.assertEqual(snapshot["battery_discharge_balance_target_distribution_mode"], "capacity_reserve_weighted")
        self.assertEqual(snapshot["battery_discharge_balance_error_w"], 500.0)
        self.assertEqual(snapshot["battery_discharge_balance_active_source_count"], 1)
        self.assertEqual(snapshot["battery_discharge_balance_control_candidate_count"], 1)
        self.assertEqual(snapshot["battery_discharge_balance_control_ready_count"], 1)
        self.assertEqual(battery_sources[0]["discharge_balance_target_power_w"], 1000.0)
        self.assertEqual(battery_sources[1]["discharge_balance_target_power_w"], 500.0)
        self.assertEqual(battery_sources[0]["discharge_balance_control_support"], "unsupported")
        self.assertEqual(battery_sources[1]["discharge_balance_control_support"], "experimental")

    def test_helper_source_helpers_cover_invalidations_dict_init_and_non_primary_errors(self):
        helper = self._make_helper()
        helper._resolved_auto_energy_services = {"primary_battery": "svc"}
        helper._auto_energy_last_scan = {"primary_battery": 10.0}
        helper._invalidate_auto_battery_service()
        self.assertNotIn("primary_battery", helper._resolved_auto_energy_services)
        self.assertNotIn("primary_battery", helper._auto_energy_last_scan)

        helper = self._make_helper()
        helper._resolved_auto_energy_services = {"primary_battery": "svc"}
        helper._auto_energy_last_scan = None
        helper._invalidate_auto_battery_service()
        self.assertNotIn("primary_battery", helper._resolved_auto_energy_services)

        helper = self._make_helper()
        helper._resolved_auto_energy_services = None
        helper._auto_energy_last_scan = {"primary_battery": 10.0}
        helper._invalidate_auto_battery_service()
        self.assertNotIn("primary_battery", helper._auto_energy_last_scan)

        helper = self._make_helper()
        helper.auto_energy_sources = (
            EnergySourceDefinition(
                source_id="primary_battery",
                role="battery",
                connector_type="dbus",
                service_name="configured-primary",
                service_prefix="com.victronenergy.battery",
                soc_path="/Soc",
            ),
            EnergySourceDefinition(
                source_id="secondary",
                role="hybrid-inverter",
                connector_type="dbus",
                service_name="configured-secondary",
                service_prefix="com.victronenergy.hybrid",
                soc_path="/Soc",
            ),
        )
        helper._energy_source_has_readable_data = MagicMock(return_value=True)
        helper._list_dbus_services = MagicMock(return_value=["configured-secondary"])
        helper._resolved_auto_energy_services = None
        helper._auto_energy_last_scan = None
        self.assertEqual(helper._resolve_energy_source_service(helper.auto_energy_sources[1]), "configured-secondary")
        self.assertIsInstance(helper._resolved_auto_energy_services, dict)
        self.assertIsInstance(helper._auto_energy_last_scan, dict)

        helper = self._make_helper()
        helper.auto_energy_sources = (
            EnergySourceDefinition(source_id="primary_battery", role="battery", connector_type="dbus"),
        )
        helper._resolved_auto_energy_services = None
        helper._auto_energy_last_scan = None
        helper._list_dbus_services = MagicMock(return_value=["com.victronenergy.battery.socketcan_can9"])
        helper._battery_service_has_soc = MagicMock(return_value=True)
        discovered = helper._discovered_auto_battery_service(100.0)
        self.assertEqual(discovered, "com.victronenergy.battery.socketcan_can9")
        self.assertEqual(helper._resolved_auto_energy_services["primary_battery"], discovered)

        source = EnergySourceDefinition(
            source_id="secondary",
            role="hybrid-inverter",
            connector_type="dbus",
            service_name="svc",
            soc_path="/Soc",
        )
        helper._resolve_energy_source_service = MagicMock(return_value="svc")
        helper._read_optional_energy_value = MagicMock(side_effect=RuntimeError("offline"))
        with self.assertRaisesRegex(RuntimeError, "offline"):
            helper._dbus_energy_source_snapshot(source, 1.0)

    def test_helper_battery_validation_and_optional_paths_cover_edge_cases(self):
        helper = self._make_helper()
        self.assertEqual(helper._validated_battery_soc(55.0, "svc"), 55.0)
        self.assertIsNone(helper._read_optional_energy_value("svc", ""))
        self.assertEqual(helper._read_optional_energy_text("svc", ""), "")

        helper._get_dbus_value = MagicMock(side_effect=[None, "support"])
        self.assertEqual(helper._read_optional_energy_text("svc", "/Mode"), "")
        self.assertEqual(helper._read_optional_energy_text("svc", "/Mode"), "support")

        helper._warning_throttled = MagicMock()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._validated_battery_soc(150.0, "svc"))
        helper._warning_throttled.assert_called_once()
        helper._delay_source_retry.assert_called_once_with("battery")

    def test_helper_source_resolution_and_configured_battery_edges_cover_remaining_branches(self):
        helper = self._make_helper()
        helper._resolved_auto_energy_services = {}
        helper._auto_energy_last_scan = {}
        source = EnergySourceDefinition(
            source_id="prefixed",
            role="hybrid-inverter",
            connector_type="dbus",
            service_prefix="com.victronenergy.hybrid",
            soc_path="/Soc",
        )
        helper._list_dbus_services = MagicMock(return_value=["com.victronenergy.hybrid.demo"])
        helper._energy_source_has_readable_data = MagicMock(return_value=True)
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=42.0):
            self.assertEqual(helper._resolve_energy_source_service(source), "com.victronenergy.hybrid.demo")

        with patch("venus_evcharger_auto_input_helper.time.time", return_value=42.0):
            self.assertIsNone(helper._cached_energy_service("missing", 42.0))

        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._resolved_auto_energy_services = None
        helper._auto_energy_last_scan = None
        helper._energy_source_has_readable_data = MagicMock(return_value=True)
        helper._list_dbus_services = MagicMock(return_value=["configured-battery"])
        self.assertEqual(helper._configured_auto_battery_service(100.0), "configured-battery")
        self.assertIsInstance(helper._resolved_auto_energy_services, dict)
        self.assertIsInstance(helper._auto_energy_last_scan, dict)

        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._energy_source_has_readable_data = MagicMock(side_effect=RuntimeError("boom"))
        helper._list_dbus_services = MagicMock(return_value=["configured-battery"])
        self.assertIsNone(helper._configured_auto_battery_service(100.0))

        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        helper._energy_source_has_readable_data = MagicMock(return_value=True)
        self.assertIsNone(helper._configured_auto_battery_service(100.0))
        helper._energy_source_has_readable_data.assert_not_called()

        helper = self._make_helper()
        unresolved_source = EnergySourceDefinition(source_id="missing", role="battery", connector_type="dbus")
        with self.assertRaisesRegex(ValueError, "No readable DBus service configured"):
            helper._resolve_energy_source_service(unresolved_source)

        helper = self._make_helper()
        unresolved_prefixed_source = EnergySourceDefinition(
            source_id="prefixed",
            role="battery",
            connector_type="dbus",
            service_prefix="com.victronenergy.battery",
        )
        helper._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        helper._energy_source_has_readable_data = MagicMock(return_value=False)
        with self.assertRaisesRegex(ValueError, "No DBus service found"):
            helper._resolve_energy_source_service(unresolved_prefixed_source)

    def test_dbus_energy_source_snapshot_retries_primary_and_handles_invalid_soc(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            battery_power_path="/Dc/0/Power",
        )
        helper._resolve_energy_source_service = MagicMock(side_effect=["svc-a", "svc-b"])
        helper._invalidate_auto_battery_service = MagicMock()
        helper._warning_throttled = MagicMock()
        helper._delay_source_retry = MagicMock()

        def _read(service_name, path):
            if service_name == "svc-a":
                raise RuntimeError("offline")
            if path == "/Soc":
                return 150.0
            return 200.0

        helper._get_dbus_value = MagicMock(side_effect=_read)

        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            snapshot = helper._dbus_energy_source_snapshot(source, 100.0)

        self.assertEqual(snapshot.service_name, "svc-b")
        self.assertIsNone(snapshot.soc)
        helper._invalidate_auto_battery_service.assert_called_once_with()
        helper._warning_throttled.assert_called_once()
        helper._delay_source_retry.assert_called_once_with("battery")
