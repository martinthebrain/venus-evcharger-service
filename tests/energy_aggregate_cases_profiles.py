# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_aggregate_cases_common import *


class _EnergyAggregateProfileCases:
    def test_derive_discharge_balance_metrics_reports_capacity_reserve_weighted_imbalance(self) -> None:
        metrics = derive_discharge_balance_metrics(
            (
                EnergySourceSnapshot(
                    source_id="victron",
                    role="battery",
                    service_name="svc-victron",
                    soc=80.0,
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
            ),
            {
                "victron": {"reserve_band_floor_soc": 40.0},
                "huawei": {"reserve_band_floor_soc": 20.0},
            },
        )

        self.assertEqual(metrics["mode"], "capacity_reserve_weighted")
        self.assertEqual(metrics["target_distribution_mode"], "capacity_reserve_weighted")
        self.assertEqual(metrics["eligible_source_count"], 2)
        self.assertEqual(metrics["active_source_count"], 1)
        self.assertEqual(metrics["total_discharge_w"], 1500.0)
        self.assertEqual(metrics["error_w"], 500.0)
        self.assertEqual(metrics["max_abs_error_w"], 500.0)
        self.assertEqual(
            metrics["sources"]["victron"]["discharge_balance_target_distribution_mode"],
            "capacity_reserve_weighted",
        )
        self.assertEqual(metrics["sources"]["victron"]["discharge_balance_weight_basis"], "available_energy_above_reserve")
        self.assertEqual(metrics["sources"]["huawei"]["discharge_balance_weight_basis"], "available_energy_above_reserve")
        self.assertEqual(metrics["sources"]["victron"]["discharge_balance_target_power_w"], 1000.0)
        self.assertEqual(metrics["sources"]["huawei"]["discharge_balance_target_power_w"], 500.0)
        self.assertEqual(metrics["sources"]["victron"]["discharge_balance_error_w"], 500.0)
        self.assertEqual(metrics["sources"]["huawei"]["discharge_balance_error_w"], -500.0)

    def test_derive_discharge_control_metrics_exposes_profile_write_hints(self) -> None:
        metrics = derive_discharge_control_metrics(
            (
                EnergySourceSnapshot(
                    source_id="victron",
                    role="battery",
                    service_name="svc-victron",
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="huawei",
                    role="hybrid-inverter",
                    service_name="svc-huawei",
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="opendtu",
                    role="inverter",
                    service_name="svc-opendtu",
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            ),
            {
                "victron": EnergySourceDefinition(source_id="victron", profile_name="dbus-battery", role="battery"),
                "huawei": EnergySourceDefinition(
                    source_id="huawei",
                    profile_name="huawei_ma_native_ap",
                    role="hybrid-inverter",
                    connector_type="modbus",
                ),
                "opendtu": EnergySourceDefinition(
                    source_id="opendtu",
                    profile_name="opendtu-pvinverter",
                    role="inverter",
                    connector_type="opendtu_http",
                ),
            },
        )

        self.assertEqual(metrics["control_candidate_count"], 1)
        self.assertEqual(metrics["control_ready_count"], 1)
        self.assertEqual(metrics["supported_control_source_count"], 0)
        self.assertEqual(metrics["experimental_control_source_count"], 1)
        self.assertEqual(metrics["sources"]["victron"]["discharge_balance_control_support"], "unsupported")
        self.assertFalse(metrics["sources"]["victron"]["discharge_balance_control_candidate"])
        self.assertEqual(metrics["sources"]["huawei"]["discharge_balance_control_support"], "experimental")
        self.assertTrue(metrics["sources"]["huawei"]["discharge_balance_control_candidate"])
        self.assertTrue(metrics["sources"]["huawei"]["discharge_balance_control_ready"])
        self.assertEqual(metrics["sources"]["huawei"]["discharge_balance_control_reason"], "profile_write_experimental")
        self.assertEqual(metrics["sources"]["opendtu"]["discharge_balance_control_reason"], "role_not_targeted")

    def test_aggregate_energy_sources_returns_none_average_confidence_without_valid_values(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="external",
                    role="battery",
                    service_name="svc",
                    soc=None,
                    usable_capacity_wh=None,
                    online=False,
                    confidence=-1.0,
                    captured_at=1.0,
                ),
            )
        )

        self.assertIsNone(cluster.average_confidence)

    def test_aggregate_energy_sources_uses_capacity_weighted_soc_and_power_sums(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="victron",
                    role="battery",
                    service_name="com.victronenergy.battery.victron",
                    soc=40.0,
                    usable_capacity_wh=5000.0,
                    net_battery_power_w=1200.0,
                    grid_interaction_w=300.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="com.victronenergy.hybrid.hybrid",
                    soc=80.0,
                    usable_capacity_wh=10000.0,
                    net_battery_power_w=-800.0,
                    ac_power_w=3200.0,
                    pv_input_power_w=2500.0,
                    grid_interaction_w=-600.0,
                    online=True,
                    confidence=0.6,
                    captured_at=100.0,
                ),
            )
        )

        self.assertAlmostEqual(cluster.combined_soc or 0.0, 66.6666666667, places=6)
        self.assertAlmostEqual(cluster.effective_soc or 0.0, 66.6666666667, places=6)
        self.assertEqual(cluster.combined_usable_capacity_wh, 15000.0)
        self.assertEqual(cluster.combined_discharge_power_w, 1200.0)
        self.assertEqual(cluster.combined_charge_power_w, 800.0)
        self.assertIsNone(cluster.combined_charge_limit_power_w)
        self.assertIsNone(cluster.combined_discharge_limit_power_w)
        self.assertEqual(cluster.combined_net_battery_power_w, 400.0)
        self.assertEqual(cluster.combined_ac_power_w, 3200.0)
        self.assertEqual(cluster.combined_pv_input_power_w, 2500.0)
        self.assertEqual(cluster.combined_grid_interaction_w, -300.0)
        self.assertEqual(cluster.average_confidence, 0.8)
        self.assertEqual(cluster.source_count, 2)
        self.assertEqual(cluster.online_source_count, 2)
        self.assertEqual(cluster.valid_soc_source_count, 2)
        self.assertEqual(cluster.battery_source_count, 1)
        self.assertEqual(cluster.hybrid_inverter_source_count, 1)
        self.assertEqual(cluster.inverter_source_count, 0)

    def test_aggregate_energy_sources_falls_back_to_single_online_soc_without_capacity(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="primary",
                    role="battery",
                    service_name="com.victronenergy.battery.primary",
                    soc=55.0,
                    usable_capacity_wh=None,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            )
        )

        self.assertIsNone(cluster.combined_soc)
        self.assertEqual(cluster.effective_soc, 55.0)

    def test_aggregate_energy_sources_deduplicates_scoped_global_values(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="mb-unit1",
                    role="hybrid-inverter",
                    service_name="10.0.0.20",
                    soc=55.0,
                    usable_capacity_wh=7000.0,
                    net_battery_power_w=600.0,
                    ac_power_w=4200.0,
                    pv_input_power_w=5100.0,
                    grid_interaction_w=-1200.0,
                    ac_power_scope_key="10.0.0.20:502:ac",
                    pv_input_power_scope_key="10.0.0.20:502:pv",
                    grid_interaction_scope_key="10.0.0.20:502:meter",
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="mb-unit2",
                    role="hybrid-inverter",
                    service_name="10.0.0.20",
                    soc=60.0,
                    usable_capacity_wh=7000.0,
                    net_battery_power_w=-400.0,
                    ac_power_w=4200.0,
                    pv_input_power_w=5100.0,
                    grid_interaction_w=-1200.0,
                    ac_power_scope_key="10.0.0.20:502:ac",
                    pv_input_power_scope_key="10.0.0.20:502:pv",
                    grid_interaction_scope_key="10.0.0.20:502:meter",
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            )
        )

        self.assertEqual(cluster.combined_discharge_power_w, 600.0)
        self.assertEqual(cluster.combined_charge_power_w, 400.0)
        self.assertEqual(cluster.combined_ac_power_w, 4200.0)
        self.assertEqual(cluster.combined_pv_input_power_w, 5100.0)
        self.assertEqual(cluster.combined_grid_interaction_w, -1200.0)

    def test_load_energy_source_settings_supports_dynamic_sources_and_legacy_defaults(self) -> None:
        legacy_sources, use_combined = load_energy_source_settings(
            {
                "AutoBatteryService": "com.victronenergy.battery.legacy",
                "AutoBatteryServicePrefix": "com.victronenergy.battery",
                "AutoBatterySocPath": "/Soc",
                "AutoBatteryCapacityWh": "5120",
                "AutoBatteryChemistry": "lfp",
                "AutoBatteryCapacityAutoEstimate": "1",
                "AutoBatteryCapacityAhPath": "/InstalledCapacity",
                "AutoBatteryVoltagePath": "/Dc/0/Voltage",
                "AutoBatteryCapacityEstimateMinSoc": "95",
                "AutoBatteryCapacityStartupRecheckSeconds": "300",
                "AutoBatteryCapacityEstimatedWh": "4800",
                "AutoBatteryCapacityEstimatedAh": "100",
                "AutoBatteryCapacityEstimatedNominalVoltage": "48",
                "AutoBatteryCapacityEstimatedCellCount": "15",
            }
        )
        self.assertTrue(use_combined)
        self.assertEqual(len(legacy_sources), 1)
        self.assertEqual(legacy_sources[0].source_id, "primary_battery")
        self.assertEqual(legacy_sources[0].connector_type, "dbus")
        self.assertEqual(legacy_sources[0].usable_capacity_wh, 5120.0)
        self.assertEqual(legacy_sources[0].battery_chemistry, "lfp")
        self.assertTrue(legacy_sources[0].capacity_auto_estimate)
        self.assertEqual(legacy_sources[0].capacity_ah_path, "/InstalledCapacity")
        self.assertEqual(legacy_sources[0].voltage_path, "/Dc/0/Voltage")
        self.assertEqual(legacy_sources[0].capacity_estimate_min_soc, 95.0)
        self.assertEqual(legacy_sources[0].capacity_startup_recheck_seconds, 300.0)
        self.assertEqual(legacy_sources[0].estimated_capacity_wh, 4800.0)
        self.assertEqual(legacy_sources[0].estimated_capacity_ah, 100.0)
        self.assertEqual(legacy_sources[0].estimated_capacity_nominal_voltage_v, 48.0)
        self.assertEqual(legacy_sources[0].estimated_capacity_cell_count, 15)

        configured_sources, use_combined = load_energy_source_settings(
            {
                "AutoEnergySources": "victron,hybrid",
                "AutoUseCombinedBatterySoc": "0",
                "AutoEnergySource.victron.Role": "battery",
                "AutoEnergySource.victron.Service": "com.victronenergy.battery.victron",
                "AutoEnergySource.victron.SocPath": "/Soc",
                "AutoEnergySource.victron.UsableCapacityWh": "5000",
                "AutoEnergySource.victron.CapacityEstimatedWh": "9600",
                "AutoEnergySource.victron.CapacityEstimatedAh": "200",
                "AutoEnergySource.victron.CapacityEstimatedNominalVoltage": "48",
                "AutoEnergySource.victron.CapacityEstimatedCellCount": "15",
                "AutoEnergySource.hybrid.Role": "hybrid-inverter",
                "AutoEnergySource.hybrid.Type": "template_http_energy",
                "AutoEnergySource.hybrid.ConfigPath": "/data/etc/external-hybrid.ini",
                "AutoEnergySource.hybrid.Service": "com.victronenergy.hybrid.hybrid",
                "AutoEnergySource.hybrid.SocPath": "/Soc",
                "AutoEnergySource.hybrid.UsableCapacityWh": "10000",
                "AutoEnergySource.hybrid.BatteryPowerPath": "/Dc/0/Power",
                "AutoEnergySource.hybrid.AcPowerPath": "/Ac/Power",
                "AutoEnergySource.hybrid.PvPowerPath": "/Pv/Power",
                "AutoEnergySource.hybrid.GridInteractionPath": "/Grid/Power",
                "AutoEnergySource.hybrid.OperatingModePath": "/Mode",
            }
        )
        self.assertFalse(use_combined)
        self.assertEqual([source.source_id for source in configured_sources], ["victron", "hybrid"])
        self.assertEqual(configured_sources[0].estimated_capacity_wh, 9600.0)
        self.assertEqual(configured_sources[0].estimated_capacity_ah, 200.0)
        self.assertEqual(configured_sources[0].estimated_capacity_nominal_voltage_v, 48.0)
        self.assertEqual(configured_sources[0].estimated_capacity_cell_count, 15)
        self.assertEqual(configured_sources[1].role, "hybrid-inverter")
        self.assertEqual(configured_sources[1].connector_type, "template_http")
        self.assertEqual(configured_sources[1].config_path, "/data/etc/external-hybrid.ini")
        self.assertEqual(configured_sources[1].battery_power_path, "/Dc/0/Power")
        self.assertEqual(configured_sources[1].pv_power_path, "/Pv/Power")
        self.assertEqual(configured_sources[1].grid_interaction_path, "/Grid/Power")
        self.assertEqual(configured_sources[1].operating_mode_path, "/Mode")

        connector_sources, _ = load_energy_source_settings(
            {
                "AutoEnergySources": "modbus,helper",
                "AutoEnergySource.modbus.Role": "battery",
                "AutoEnergySource.modbus.Type": "modbus",
                "AutoEnergySource.modbus.ConfigPath": "/data/etc/external-modbus.ini",
                "AutoEnergySource.helper.Role": "hybrid-inverter",
                "AutoEnergySource.helper.Type": "command_json",
                "AutoEnergySource.helper.ConfigPath": "/data/etc/external-helper.ini",
            }
        )
        self.assertEqual(connector_sources[0].connector_type, "modbus")
        self.assertEqual(connector_sources[1].connector_type, "command_json")

        profile_sources, _ = load_energy_source_settings(
            {
                "AutoEnergySources": "opendtu",
                "AutoEnergySource.opendtu.Profile": "opendtu-pvinverter",
                "AutoEnergySource.opendtu.ConfigPath": "/data/etc/opendtu-energy.ini",
            }
        )
        self.assertEqual(profile_sources[0].role, "inverter")
        self.assertEqual(profile_sources[0].connector_type, "opendtu_http")
        self.assertEqual(profile_sources[0].profile_name, "opendtu-pvinverter")

    def test_load_energy_source_settings_normalizes_invalid_role_connector_and_capacity(self) -> None:
        configured_sources, use_combined = load_energy_source_settings(
            {
                "AutoEnergySources": "external",
                "AutoUseCombinedBatterySoc": "yes",
                "AutoEnergySource.external.Role": "unknown",
                "AutoEnergySource.external.Type": "unknown",
                "AutoEnergySource.external.UsableCapacityWh": "bad",
            }
        )

        self.assertTrue(use_combined)
        self.assertEqual(len(configured_sources), 1)
        self.assertEqual(configured_sources[0].role, "battery")
        self.assertEqual(configured_sources[0].connector_type, "dbus")
        self.assertIsNone(configured_sources[0].usable_capacity_wh)

    def test_load_energy_source_settings_applies_profile_defaults_and_explicit_overrides(self) -> None:
        configured_sources, use_combined = load_energy_source_settings(
            {
                "AutoEnergySources": "victron,external",
                "AutoUseCombinedBatterySoc": "1",
                "AutoEnergySource.victron.Profile": "dbus-battery",
                "AutoEnergySource.victron.Service": "com.victronenergy.battery.lynxparallel",
                "AutoEnergySource.victron.UsableCapacityWh": "10240",
                "AutoEnergySource.victron.Chemistry": "LFP",
                "AutoEnergySource.victron.CapacityAutoEstimate": "1",
                "AutoEnergySource.external.Profile": "http-hybrid",
                "AutoEnergySource.external.ConfigPath": "/data/etc/external-energy.ini",
                "AutoEnergySource.external.UsableCapacityWh": "14000",
                "AutoEnergySource.external.AcPowerPath": "/custom/ac",
            }
        )

        self.assertTrue(use_combined)
        self.assertEqual(configured_sources[0].profile_name, "dbus-battery")
        self.assertEqual(configured_sources[0].role, "battery")
        self.assertEqual(configured_sources[0].connector_type, "dbus")
        self.assertEqual(configured_sources[0].service_prefix, "com.victronenergy.battery")
        self.assertEqual(configured_sources[0].battery_power_path, "/Dc/0/Power")
        self.assertEqual(configured_sources[0].battery_chemistry, "lfp")
        self.assertTrue(configured_sources[0].capacity_auto_estimate)
        self.assertEqual(configured_sources[0].capacity_ah_path, "/InstalledCapacity")
        self.assertEqual(configured_sources[0].voltage_path, "/Dc/0/Voltage")
        self.assertEqual(configured_sources[1].profile_name, "template-http-hybrid")
        self.assertEqual(configured_sources[1].role, "hybrid-inverter")
        self.assertEqual(configured_sources[1].connector_type, "template_http")
        self.assertEqual(configured_sources[1].config_path, "/data/etc/external-energy.ini")
        self.assertEqual(configured_sources[1].ac_power_path, "/custom/ac")

    def test_energy_source_profiles_support_aliases_and_unknown_names(self) -> None:
        available = available_energy_source_profiles()
        self.assertEqual(
            available[:5],
            (
                "dbus-battery",
                "dbus-hybrid",
                "template-http-hybrid",
                "modbus-hybrid",
                "command-json-hybrid",
            ),
        )
        self.assertIn("opendtu-pvinverter", available)
        self.assertIn("huawei_ma_native_ap", available)
        self.assertIn("huawei_ma_smartlogger_modbus_tcp", available)
        self.assertIn("huawei_l1_native_ap", available)
        self.assertIn("huawei_l1_smartlogger_modbus_tcp", available)
        self.assertIn("huawei_map0_native_lan", available)
        self.assertIn("huawei_mb0_smartlogger_modbus_tcp", available)
        self.assertIn("huawei_mb_unit1", available)
        self.assertIn("huawei_map0_unit2", available)
        self.assertIn("huawei_smartlogger_modbus_tcp", available)
        self.assertEqual(resolve_energy_source_profile("helper").profile_name, "command-json-hybrid")
        self.assertEqual(resolve_energy_source_profile("opendtu").profile_name, "opendtu-pvinverter")
        self.assertIsNone(resolve_energy_source_profile("unknown-profile"))
        self.assertEqual(resolve_energy_source_profile("huawei_m1_native_ap").profile_name, "huawei_m1_native_ap")
        self.assertEqual(resolve_energy_source_profile("huawei_mb0_sdongle").profile_name, "huawei_mb0_sdongle")
        self.assertEqual(resolve_energy_source_profile("huawei_sun5000_lb0_native_ap").profile_name, "huawei_lb0_native_ap")
        self.assertEqual(resolve_energy_source_profile("huawei_sun5000_map0_unit1").profile_name, "huawei_map0_unit1")

    def test_huawei_energy_source_profiles_expose_details_and_probe_plan(self) -> None:
        details = energy_source_profile_details("huawei_ma_native_ap")
        plan = energy_source_profile_probe_plan(
            "huawei_ma_native_ap",
            configured_host="10.0.0.15",
            configured_port="6607",
            configured_unit_id=1,
        )

        self.assertEqual(details["vendor_name"], "Huawei")
        self.assertEqual(details["platform"], "MA")
        self.assertEqual(details["family_name"], "MA")
        self.assertEqual(details["access_mode"], "native_ap")
        self.assertEqual(details["default_host"], "192.168.200.1")
        self.assertEqual(details["default_port_candidates"], [6607, 502])
        self.assertEqual(details["default_unit_id_candidates"], [0, 1])
        self.assertEqual(details["write_support"], "experimental")
        self.assertTrue(details["probe_required"])
        self.assertEqual(plan["host"], "10.0.0.15")
        self.assertEqual(plan["port_candidates"], [6607])
        self.assertEqual(plan["unit_id_candidates"], [1])

    def test_opendtu_profile_exposes_inverter_metadata(self) -> None:
        details = energy_source_profile_details("growatt-opendtu")

        self.assertEqual(details["profile_name"], "opendtu-pvinverter")
        self.assertEqual(details["vendor_name"], "OpenDTU")
        self.assertEqual(details["platform"], "OpenDTU")
        self.assertEqual(details["family_name"], "OpenDTU")
        self.assertEqual(details["connector_type"], "opendtu_http")
        self.assertEqual(details["role"], "inverter")
        self.assertEqual(details["read_support"], "supported")
        self.assertEqual(details["write_support"], "unsupported")
        self.assertFalse(details["probe_required"])
        self.assertEqual(details["idle_unreachable_policy"], "allow_plausible_idle")

    def test_huawei_family_profile_details_expose_family_specific_metadata(self) -> None:
        details = energy_source_profile_details("huawei_map0_smartlogger_modbus_tcp")
        plan = energy_source_profile_probe_plan("huawei_map0_unit1")

        self.assertEqual(details["vendor_name"], "Huawei")
        self.assertEqual(details["platform"], "MB")
        self.assertEqual(details["family_name"], "MAP0")
        self.assertEqual(details["access_mode"], "smartlogger")
        self.assertEqual(details["default_port_candidates"], [502])
        self.assertEqual(plan["profile_name"], "huawei_map0_unit1")
        self.assertEqual(plan["connector_type"], "modbus")
        self.assertEqual(plan["port_candidates"], [502, 6607])

    def test_huawei_smartlogger_probe_plan_uses_defaults_without_overrides(self) -> None:
        plan = energy_source_profile_probe_plan("huawei_smartlogger_modbus_tcp")

        self.assertEqual(plan["profile_name"], "huawei_smartlogger_modbus_tcp")
        self.assertEqual(plan["connector_type"], "modbus")
        self.assertEqual(plan["host"], "")
        self.assertEqual(plan["port_candidates"], [502])
        self.assertEqual(plan["unit_id_candidates"], [0, 1])
