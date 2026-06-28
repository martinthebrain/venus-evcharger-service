# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile

from tests.auto_input_helper_sources_cases_common import *
from venus_evcharger.inputs.helper.sources_dbus_common import (
    _dbus_error_name,
    _is_expected_missing_dbus_error,
)
from venus_evcharger.inputs.energy_snapshot_contracts import (
    energy_source_definitions,
    is_source_definition_iterable,
    learning_profile_payloads,
    learning_profiles,
    nested_object_mappings,
    object_mapping,
)
from venus_evcharger.inputs.helper.sources_dbus import _AutoInputHelperSourceDbusMixin
from venus_evcharger.inputs.helper.subscriptions import _AutoInputHelperSubscriptionMixin


class _AutoInputHelperSourcesEnergyCases:
    def test_battery_snapshot_normalizers_reject_dirty_payloads(self):
        source = EnergySourceDefinition(source_id="battery", role="battery", connector_type="dbus")
        profile = EnergyLearningProfile(source_id="battery", sample_count=3)

        self.assertEqual(energy_source_definitions(source), (source,))
        self.assertEqual(energy_source_definitions([source, "bad", object()]), (source,))
        self.assertEqual(energy_source_definitions("bad"), ())
        self.assertEqual(energy_source_definitions(object()), ())
        self.assertFalse(is_source_definition_iterable({"source": source}))

        self.assertEqual(object_mapping("bad"), {})
        self.assertEqual(object_mapping({1: "one"}), {"1": "one"})
        self.assertEqual(nested_object_mappings("bad"), {})
        self.assertEqual(nested_object_mappings({"battery": {"ready": True}, "skip": "bad"}), {"battery": {"ready": True}})

        self.assertEqual(learning_profiles("bad"), {})
        self.assertEqual(
            set(learning_profiles({"battery": profile, "legacy": {"sample_count": 1}, "skip": object()})),
            {"battery", "legacy"},
        )
        self.assertEqual(learning_profile_payloads("bad"), {})
        self.assertEqual(learning_profile_payloads({"battery": profile, "skip": object()})["battery"]["source_id"], "battery")

    def test_mixin_default_dbus_module_fallbacks_are_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
            _AutoInputHelperSourceDbusMixin._dbus_module()
        with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
            _AutoInputHelperSubscriptionMixin._dbus_module()

    def test_dbus_gateway_common_error_helpers_cover_name_and_text_matching(self):
        class NamedDbusError(Exception):
            def get_dbus_name(self):
                return "org.freedesktop.DBus.Error.ServiceUnknown"

        class AttributeDbusError(Exception):
            _dbus_error_name = "org.freedesktop.DBus.Error.UnknownObject"

        self.assertEqual(_dbus_error_name(NamedDbusError("missing")), "org.freedesktop.DBus.Error.ServiceUnknown")
        self.assertEqual(_dbus_error_name(AttributeDbusError("missing")), "org.freedesktop.DBus.Error.UnknownObject")
        self.assertTrue(_is_expected_missing_dbus_error(NamedDbusError("missing")))
        self.assertTrue(_is_expected_missing_dbus_error(RuntimeError("NameHasNoOwner")))
        self.assertFalse(_is_expected_missing_dbus_error(RuntimeError("temporary timeout")))

    def test_gateway_request_retry_and_introspection_helpers_cover_edge_paths(self):
        helper = self._make_helper()
        helper._gateway_client = MagicMock()
        helper._gateway_client.return_value.enqueue_command.side_effect = OSError("socket down")

        helper._request_dbus_introspection("svc", "/Path", priority=80, reason="test")
        helper._request_gateway_value("svc", "/Path", priority=90, reason="test")
        self.assertEqual(helper._gateway_client.return_value.enqueue_command.call_count, 2)

        calls: list[int] = []

        def flaky_read():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("temporary")
            return 7

        helper._reset_system_bus = MagicMock()
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.logging.debug") as debug:
            self.assertEqual(helper._dbus_retry_read("svc", "/Value", "read", flaky_read), 7)
        helper._reset_system_bus.assert_called_once_with()
        debug.assert_called_once()

        with self.assertRaisesRegex(RuntimeError, "ServiceUnknown"):
            helper._dbus_retry_read("svc", "/Missing", "read", lambda: (_ for _ in ()).throw(RuntimeError("ServiceUnknown")))

        with self.assertRaisesRegex(RuntimeError, "temporary"):
            helper._dbus_retry_read("svc", "/Flaky", "read", lambda: (_ for _ in ()).throw(RuntimeError("temporary")))

        helper._reset_system_bus_after_retryable_error(1, "read", "svc", "/Value", RuntimeError("later"))

        self.assertEqual(
            helper._child_nodes_from_introspection("<node><node name='A'/><node/><node name='B'/></node>"),
            ["A", "B"],
        )

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

        helper._get_dbus_value = MagicMock(side_effect=[True, "bad", 12.5])
        self.assertIsNone(helper._read_optional_energy_value("svc", "/Soc"))
        self.assertIsNone(helper._read_optional_energy_value("svc", "/Soc"))
        self.assertEqual(helper._read_optional_energy_value("svc", "/Soc"), 12.5)
        self.assertIsNone(helper._battery_soc_numeric("bad"))
        self.assertEqual(helper._battery_soc_numeric("12.5"), 12.5)

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
        helper._resolved_auto_energy_services = {"numeric": 123}
        helper._auto_energy_last_scan = {"numeric": 40.0}
        self.assertEqual(helper._cached_energy_service("numeric", 42.0), "123")

        helper = self._make_helper()
        helper._resolve_auto_battery_service = MagicMock(return_value="")
        primary_source = EnergySourceDefinition(source_id="primary_battery", role="battery", connector_type="dbus")
        with self.assertRaisesRegex(ValueError, "primary energy source"):
            helper._resolve_energy_source_service(primary_source)

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
        unreadable_source = EnergySourceDefinition(
            source_id="secondary",
            role="battery",
            connector_type="dbus",
            service_name="configured-secondary",
        )
        helper._dbus_service_name_available = MagicMock(return_value=True)
        helper._energy_source_has_readable_data = MagicMock(return_value=False)
        self.assertIsNone(helper._configured_energy_source_service(unreadable_source, 100.0))

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

    def test_dbus_energy_source_snapshot_infers_lfp_capacity_from_full_soc_voltage(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            capacity_ah_path="/InstalledCapacity",
            voltage_path="/Dc/0/Voltage",
            capacity_estimate_min_soc=95.0,
            capacity_startup_recheck_seconds=300.0,
        )
        helper._auto_battery_capacity_startup_recheck_at = 400.0
        helper._resolve_energy_source_service = MagicMock(return_value="svc")
        values = {
            "/Soc": 100.0,
            "/InstalledCapacity": 100.0,
            "/Dc/0/Voltage": 50.7,
        }
        helper._get_dbus_value = MagicMock(side_effect=lambda _service, path: values[path])

        snapshot = helper._dbus_energy_source_snapshot(source, 100.0)

        self.assertEqual(snapshot.usable_capacity_wh, 4800.0)
        self.assertEqual(snapshot.usable_capacity_source, "dbus_lfp_inferred")
        self.assertEqual(snapshot.installed_capacity_ah, 100.0)
        self.assertEqual(snapshot.capacity_voltage_v, 50.7)
        self.assertEqual(snapshot.capacity_nominal_voltage_v, 48.0)
        self.assertEqual(snapshot.capacity_cell_count, 15)

        values.update({"/InstalledCapacity": 200.0, "/Dc/0/Voltage": 54.0})
        cached = helper._dbus_energy_source_snapshot(source, 120.0)
        self.assertEqual(cached.usable_capacity_wh, 4800.0)
        self.assertEqual(cached.capacity_cell_count, 15)

        rechecked = helper._dbus_energy_source_snapshot(source, 401.0)
        self.assertEqual(rechecked.usable_capacity_wh, 10240.0)
        self.assertEqual(rechecked.capacity_nominal_voltage_v, 51.2)
        self.assertEqual(rechecked.capacity_cell_count, 16)

        values.update({"/InstalledCapacity": 300.0, "/Dc/0/Voltage": 50.7})
        after_startup_recheck = helper._dbus_energy_source_snapshot(source, 800.0)
        self.assertEqual(after_startup_recheck.usable_capacity_wh, 10240.0)
        self.assertEqual(after_startup_recheck.capacity_cell_count, 16)

    def test_dbus_capacity_inference_waits_for_high_soc_and_lfp(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            capacity_ah_path="/InstalledCapacity",
            voltage_path="/Dc/0/Voltage",
        )
        helper._resolve_energy_source_service = MagicMock(return_value="svc")
        helper._get_dbus_value = MagicMock(side_effect=lambda _service, path: {"/Soc": 80.0}[path])
        low_soc = helper._dbus_energy_source_snapshot(source, 100.0)
        self.assertIsNone(low_soc.usable_capacity_wh)
        self.assertEqual(low_soc.usable_capacity_source, "")

        helper = self._make_helper()
        non_lfp = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            battery_chemistry="nmc",
            capacity_ah_path="/InstalledCapacity",
            voltage_path="/Dc/0/Voltage",
        )
        helper._resolve_energy_source_service = MagicMock(return_value="svc")
        helper._get_dbus_value = MagicMock(side_effect=lambda _service, path: {"/Soc": 100.0}[path])
        snapshot = helper._dbus_energy_source_snapshot(non_lfp, 100.0)
        self.assertIsNone(snapshot.usable_capacity_wh)
        self.assertEqual(snapshot.battery_chemistry, "nmc")

    def test_dbus_capacity_recheck_persists_only_when_installed_ah_changes(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            capacity_ah_path="/InstalledCapacity",
            voltage_path="/Dc/0/Voltage",
            capacity_estimate_min_soc=95.0,
            capacity_startup_recheck_seconds=300.0,
            estimated_capacity_wh=4800.0,
            estimated_capacity_ah=100.0,
            estimated_capacity_nominal_voltage_v=48.0,
            estimated_capacity_cell_count=15,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = f"{temp_dir}/config.ini"
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[DEFAULT]\n"
                    "# keep me\n"
                    "AutoBatteryCapacityEstimatedWh=4800\n"
                    "AutoBatteryCapacityEstimatedAh=100\n"
                    "AutoBatteryCapacityEstimatedNominalVoltage=48\n"
                    "AutoBatteryCapacityEstimatedCellCount=15\n"
                )
            helper.config_path = config_path
            helper._auto_battery_capacity_startup_recheck_at = 400.0
            helper._resolve_energy_source_service = MagicMock(return_value="svc")
            values = {
                "/Soc": 100.0,
                "/InstalledCapacity": 100.0,
                "/Dc/0/Voltage": 54.0,
            }
            helper._get_dbus_value = MagicMock(side_effect=lambda _service, path: values[path])

            unchanged = helper._dbus_energy_source_snapshot(source, 401.0)
            with open(config_path, encoding="utf-8") as handle:
                unchanged_text = handle.read()
            self.assertEqual(unchanged.usable_capacity_wh, 5120.0)
            self.assertIn("# keep me", unchanged_text)
            self.assertIn("AutoBatteryCapacityEstimatedAh=100", unchanged_text)
            self.assertIn("AutoBatteryCapacityEstimatedCellCount=15", unchanged_text)

            helper._auto_battery_capacity_startup_rechecked = {}
            values["/InstalledCapacity"] = 200.0
            changed = helper._dbus_energy_source_snapshot(source, 402.0)
            with open(config_path, encoding="utf-8") as handle:
                changed_text = handle.read()
            self.assertEqual(changed.usable_capacity_wh, 10240.0)
            self.assertIn("# keep me", changed_text)
            self.assertIn("AutoBatteryCapacityEstimatedAh=200", changed_text)
            self.assertIn("AutoBatteryCapacityEstimatedWh=10240", changed_text)
            self.assertIn("AutoBatteryCapacityEstimatedNominalVoltage=51.2", changed_text)
            self.assertIn("AutoBatteryCapacityEstimatedCellCount=16", changed_text)

    def test_dbus_capacity_uses_persisted_estimate_before_startup_recheck(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            soc_path="/Soc",
            capacity_ah_path="/InstalledCapacity",
            voltage_path="/Dc/0/Voltage",
            estimated_capacity_wh=4800.0,
            estimated_capacity_ah=100.0,
            estimated_capacity_nominal_voltage_v=48.0,
            estimated_capacity_cell_count=15,
        )
        helper._auto_battery_capacity_startup_recheck_at = 400.0
        helper._resolve_energy_source_service = MagicMock(return_value="svc")
        values = {
            "/Soc": 80.0,
            "/InstalledCapacity": 200.0,
            "/Dc/0/Voltage": 54.0,
        }
        helper._get_dbus_value = MagicMock(side_effect=lambda _service, path: values[path])

        snapshot = helper._dbus_energy_source_snapshot(source, 100.0)

        self.assertEqual(snapshot.usable_capacity_wh, 4800.0)
        self.assertEqual(snapshot.usable_capacity_source, "config_estimated")
        self.assertEqual(snapshot.installed_capacity_ah, 100.0)

    def test_dbus_capacity_payload_prefers_configured_capacity_and_direct_dbus_capacity(self):
        helper = self._make_helper()
        configured = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            usable_capacity_wh=7000.0,
        )
        self.assertEqual(
            helper._dbus_energy_source_capacity_payload(configured, "svc", 100.0, 100.0),
            {"usable_capacity_wh": 7000.0, "usable_capacity_source": "configured"},
        )

        direct = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            capacity_wh_path="/Capacity",
        )
        helper._get_dbus_value = MagicMock(side_effect=lambda _service, path: {"/Capacity": 6000.0}[path])
        payload = helper._dbus_energy_source_capacity_payload(direct, "svc", 100.0, 100.0)
        self.assertEqual(payload, {"usable_capacity_wh": 6000.0, "usable_capacity_source": "dbus_capacity_wh"})

    def test_dbus_capacity_helpers_cover_cache_recheck_and_invalid_runtime_state(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            capacity_ah_path="/InstalledCapacity",
            voltage_path="/Dc/0/Voltage",
            capacity_startup_recheck_seconds=0.0,
        )
        self.assertFalse(helper._dbus_capacity_startup_recheck_due(source, "svc", 100.0))

        active_source = EnergySourceDefinition(**{**source.__dict__, "capacity_startup_recheck_seconds": 1.0})
        helper._auto_battery_capacity_startup_recheck_at = 0.0
        self.assertFalse(helper._dbus_capacity_startup_recheck_due(active_source, "svc", 100.0))
        helper._auto_battery_capacity_startup_recheck_at = 200.0
        self.assertFalse(helper._dbus_capacity_startup_recheck_due(active_source, "svc", 100.0))
        self.assertTrue(helper._dbus_capacity_startup_recheck_due(active_source, "svc", 201.0))
        helper._auto_battery_capacity_startup_rechecked = {helper._dbus_capacity_cache_key(active_source, "svc"): True}
        self.assertFalse(helper._dbus_capacity_startup_recheck_due(active_source, "svc", 202.0))

        helper._auto_battery_capacity_estimates = None
        helper._auto_battery_capacity_startup_rechecked = None
        helper._store_dbus_capacity_payload(active_source, "svc", {"usable_capacity_wh": 1.0}, startup_recheck_done=True)
        self.assertEqual(helper._cached_dbus_capacity_payload(active_source, "svc"), {"usable_capacity_wh": 1.0})
        self.assertEqual(helper._resolved_dbus_capacity_payload(None, {"usable_capacity_wh": 2.0}), {"usable_capacity_wh": 2.0})

        cache_key = helper._dbus_capacity_cache_key(active_source, "svc")
        helper._auto_battery_capacity_estimates = {
            cache_key: {
                "usable_capacity_wh": "bad",
                "installed_capacity_ah": True,
                "capacity_voltage_v": 54.0,
                "capacity_cell_count": "16",
                7: "numeric-key",
            }
        }
        cached_payload = helper._cached_dbus_capacity_payload(active_source, "svc")
        self.assertEqual(cached_payload["7"], "numeric-key")

        helper._dbus_energy_source_capacity_payload = MagicMock(return_value=cached_payload)
        snapshot = helper._dbus_energy_source_snapshot_payload(
            active_source,
            "svc",
            95.0,
            None,
            None,
            None,
            None,
            "",
            123.0,
        )
        self.assertIsNone(snapshot.usable_capacity_wh)
        self.assertIsNone(snapshot.installed_capacity_ah)
        self.assertEqual(snapshot.capacity_voltage_v, 54.0)
        self.assertEqual(snapshot.capacity_cell_count, 16)

    def test_dbus_capacity_persistence_handles_disabled_and_failed_writes(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(source_id="primary_battery", role="battery", connector_type="dbus")
        with patch("venus_evcharger.inputs.helper.sources_dbus_snapshot.persist_estimated_capacity_if_ah_changed") as persist:
            helper._persist_dbus_capacity_payload_if_needed(source, {"installed_capacity_ah": 100.0}, False)
            persist.assert_not_called()

        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_snapshot.persist_estimated_capacity_if_ah_changed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("venus_evcharger.inputs.helper.sources_dbus_snapshot.logging.warning") as warning:
                helper._persist_dbus_capacity_payload_if_needed(source, {"installed_capacity_ah": 100.0}, True)
        warning.assert_called_once()

    def test_dbus_capacity_inference_handles_invalid_capacity_inputs_and_voltage_bounds(self):
        helper = self._make_helper()
        source = EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            connector_type="dbus",
            capacity_ah_path="/InstalledCapacity",
            voltage_path="/Dc/0/Voltage",
        )
        helper._get_dbus_value = MagicMock(side_effect=lambda _service, path: {"/InstalledCapacity": 100.0, "/Dc/0/Voltage": 39.0}[path])
        self.assertIsNone(helper._infer_dbus_capacity_payload(source, "svc", 100.0))
        self.assertEqual(helper._lfp_nominal_voltage_from_full_voltage(60.1), (None, None))
        self.assertEqual(helper._lfp_nominal_voltage_from_full_voltage(None), (None, None))

    def test_primary_estimated_capacity_getters_tolerate_invalid_values(self):
        helper = self._make_helper()
        helper.auto_battery_capacity_estimated_wh = "bad"
        helper.auto_battery_capacity_estimated_ah = -1
        helper.auto_battery_capacity_estimated_nominal_voltage = None
        helper.auto_battery_capacity_estimated_cell_count = "bad"

        self.assertIsNone(helper._primary_energy_estimated_capacity_wh())
        self.assertIsNone(helper._primary_energy_estimated_capacity_ah())
        self.assertIsNone(helper._primary_energy_estimated_capacity_nominal_voltage())
        self.assertIsNone(helper._primary_energy_estimated_capacity_cell_count())

        source = EnergySourceDefinition(source_id="primary_battery", role="battery", connector_type="dbus")
        helper.auto_energy_sources = [source, "not-a-source"]
        self.assertEqual(helper._configured_primary_energy_sources(), (source,))
        helper.auto_energy_sources = {"source": source}
        self.assertEqual(helper._configured_primary_energy_sources(), ())
        helper.auto_energy_sources = "not-a-sequence"
        self.assertEqual(helper._configured_primary_energy_sources(), ())
