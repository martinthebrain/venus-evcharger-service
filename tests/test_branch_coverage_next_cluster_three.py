# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace

from venus_evcharger.energy import connectors_opendtu as opendtu_mod
from venus_evcharger.energy import connectors_opendtu_payload as opendtu_payload
from venus_evcharger.energy import probe_huawei as probe_huawei_mod
from venus_evcharger.energy import recommendation_schema as recommendation_schema_mod
from venus_evcharger.energy.connectors_common import _runtime_cache_put
from venus_evcharger.energy.models import EnergySourceDefinition


class BranchCoverageNextClusterThreeOpenDtuCases(unittest.TestCase):
    def test_opendtu_helper_branches_cover_selection_confidence_and_idle_paths(self) -> None:
        source = EnergySourceDefinition(
            source_id="pv",
            role="inverter",
            profile_name="unknown-profile",
            connector_type="opendtu_http",
        )
        strict_source = EnergySourceDefinition(
            source_id="battery",
            role="battery",
            profile_name="unknown-profile",
            connector_type="opendtu_http",
        )

        self.assertEqual(opendtu_mod._opendtu_source_name(source, SimpleNamespace(base_url="", status_url="")), "pv")
        self.assertEqual(
            opendtu_mod._opendtu_source_name(
                EnergySourceDefinition(
                    source_id="pv", role="inverter", connector_type="opendtu_http", config_path="/tmp/opendtu.ini"
                ),
                SimpleNamespace(base_url="", status_url=""),
            ),
            "/tmp/opendtu.ini",
        )
        self.assertEqual(
            opendtu_mod._opendtu_timeout_seconds(
                SimpleNamespace(shelly_request_timeout_seconds=2.0), {"RequestTimeoutSeconds": "0"}
            ),
            2.0,
        )
        self.assertEqual(opendtu_mod._opendtu_max_data_age_seconds({"MaxDataAgeSeconds": "-1"}), 600.0)

        cached_settings = opendtu_mod.OpenDtuEnergySourceSettings(
            base_url="http://cached.local",
            auth_settings=SimpleNamespace(),
            timeout_seconds=2.0,
            status_url="http://cached.local/status",
            inverter_status_url="http://cached.local/status?inv=${serial}",
            serial_filter=(),
            max_data_age_seconds=600.0,
        )
        runtime = SimpleNamespace()
        _runtime_cache_put(
            runtime,
            "opendtu.settings",
            "/tmp/cached.ini",
            cached_settings,
        )
        cached_source = EnergySourceDefinition(
            source_id="pv",
            role="inverter",
            connector_type="opendtu_http",
            config_path="/tmp/cached.ini",
        )
        self.assertIs(opendtu_mod._opendtu_energy_source_settings(runtime, cached_source), cached_settings)
        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            opendtu_mod._opendtu_energy_source_settings(
                SimpleNamespace(),
                EnergySourceDefinition(source_id="pv", role="inverter", connector_type="opendtu_http"),
            )
        with self.assertRaisesRegex(ValueError, "requires OpenDTU.StatusUrl"):
            opendtu_mod._validate_opendtu_energy_source_settings(
                source,
                opendtu_mod.OpenDtuEnergySourceSettings(
                    base_url="",
                    auth_settings=SimpleNamespace(),
                    timeout_seconds=2.0,
                    status_url="",
                    inverter_status_url="",
                    serial_filter=(),
                    max_data_age_seconds=600.0,
                ),
            )

        self.assertEqual(
            opendtu_payload.opendtu_unique_raw_inverters({}, ()),
            (),
        )
        self.assertFalse(opendtu_payload.opendtu_filtered_raw_inverter("bad", ()))
        self.assertFalse(opendtu_payload.opendtu_matches_serial_filter({"serial": "a"}, ("b",)))
        with self.assertRaisesRegex(ValueError, "missing inverter a"):
            opendtu_payload.opendtu_detail_inverter({}, "a")
        with self.assertRaisesRegex(ValueError, "uniquely identify inverter a"):
            opendtu_payload.opendtu_detail_inverter(
                {"inverters": [{"serial": "a"}, {"serial": "a"}]},
                "a",
            )
        self.assertEqual(
            opendtu_payload.opendtu_summed_ac_power(
                ({"AC": {"0": {"Power": {"v": 12.0}}}},),
            ),
            12.0,
        )
        self.assertTrue(opendtu_payload.energy_source_allows_unreachable_idle(source))
        self.assertFalse(opendtu_payload.energy_source_allows_unreachable_idle(strict_source))
        self.assertTrue(
            opendtu_payload.energy_source_allows_unreachable_idle(
                EnergySourceDefinition(
                    source_id="pv2",
                    role="inverter",
                    profile_name="opendtu-pvinverter",
                    connector_type="opendtu_http",
                )
            )
        )
        self.assertFalse(
            opendtu_payload.energy_source_allows_unreachable_idle(
                EnergySourceDefinition(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    profile_name="huawei_mb_native_lan",
                    connector_type="opendtu_http",
                )
            )
        )
        self.assertFalse(opendtu_payload.opendtu_inverter_online({"reachable": True}, 10.0))
        self.assertFalse(opendtu_payload.opendtu_inverter_online({"reachable": True, "data_age": 11}, 10.0))
        self.assertIsNone(opendtu_payload.opendtu_ac_power({"AC": "bad"}))
        self.assertIsNone(opendtu_payload.opendtu_ac_power({"AC": {"0": "bad"}}))
        self.assertIsNone(opendtu_payload.opendtu_ac_power({"AC": {}}))
        self.assertIsNone(opendtu_payload.opendtu_dc_power({"DC": {"0": "bad"}}))
        self.assertIsNone(opendtu_payload.opendtu_dc_power({"DC": "bad"}))
        self.assertIsNone(opendtu_payload.opendtu_dc_power({"DC": {"0": {"Power": "bad"}}}))
        self.assertIsNone(opendtu_payload.opendtu_metric_value({"Power": "bad"}, "Power"))

        online, confidence = opendtu_payload.opendtu_snapshot_confidence(
            (
                {"reachable": False, "producing": False},
                {"reachable": True, "producing": False, "data_age": 1},
            ),
            10.0,
            False,
        )
        self.assertTrue(online)
        self.assertEqual(confidence, 0.5)
        online, confidence = opendtu_payload.opendtu_snapshot_confidence(
            ({"reachable": False, "producing": False},), 10.0, True
        )
        self.assertTrue(online)
        self.assertEqual(confidence, 1.0)

        self.assertTrue(
            opendtu_payload.opendtu_plausible_idle_snapshot(
                {"hints": {}},
                ({"reachable": False, "producing": False},),
                ac_power_w=0.0,
                pv_input_power_w=0.0,
                max_data_age_seconds=10.0,
                allow_unreachable_idle=True,
            )
        )
        self.assertFalse(
            opendtu_payload.opendtu_plausible_idle_snapshot(
                {"hints": {"radio_problem": True}},
                ({"reachable": False, "producing": False},),
                ac_power_w=0.0,
                pv_input_power_w=0.0,
                max_data_age_seconds=10.0,
                allow_unreachable_idle=True,
            )
        )


class BranchCoverageNextClusterThreeHuaweiCases(unittest.TestCase):
    def test_huawei_recommendation_helpers_cover_template_and_text_fallbacks(self) -> None:
        detected = {"host": "198.51.100.2", "port": "502", "unit_id": "1"}
        detection = {"detected": detected, "profile_details": {"platform": "MB", "access_mode": "native"}}
        recommendation = probe_huawei_mod._huawei_recommendation(
            "huawei_mb_native_lan",
            detection=detection,
            required_fields_ok=False,
            meter_block_detected=False,
            source_id="",
        )
        self.assertEqual(recommendation["status"], "incomplete")
        self.assertEqual(recommendation["capacity_config_key"], "AutoEnergySource.huawei.UsableCapacityWh")
        self.assertIn("meter block missing", recommendation["summary"])

        self.assertEqual(
            probe_huawei_mod._recommended_huawei_template("huawei_map0_unit2"),
            "deploy/venus/template-energy-source-huawei-mb-unit2-modbus.ini",
        )
        self.assertEqual(
            probe_huawei_mod._recommended_huawei_template("huawei_ma_native"),
            "deploy/venus/template-energy-source-huawei-ma-modbus.ini",
        )
        self.assertEqual(
            probe_huawei_mod._recommended_huawei_config_path(
                "deploy/venus/template-energy-source-huawei-mb-modbus.ini"
            ),
            "/data/etc/huawei-mb-modbus.ini",
        )
        self.assertEqual(
            probe_huawei_mod._recommended_huawei_config_path("custom-huawei.ini"),
            "/data/etc/custom-huawei.ini",
        )
        self.assertEqual(
            probe_huawei_mod._recommendation_summary("profile-x", {}, True, "template.ini"),
            "Use profile profile-x with template template.ini; host=unknown; meter block present.",
        )
        self.assertIn(
            "AutoEnergySource.hybrid.Profile=profile-x",
            probe_huawei_mod._recommendation_config_snippet(
                "profile-x",
                {},
                template_name="template.ini",
                config_path="/data/etc/huawei.ini",
                source_id="hybrid",
            ),
        )
        self.assertIn(
            "- port: unknown",
            probe_huawei_mod._recommendation_wizard_hint_block(
                "profile-x",
                {},
                meter_block_detected=False,
                template_name="template.ini",
                config_path="/data/etc/huawei.ini",
                source_id="hybrid",
            ),
        )
        self.assertEqual(probe_huawei_mod._mapping_value({"x": []}, "x"), {})
        self.assertEqual(
            probe_huawei_mod._recommendation_notes(meter_block_detected=False, required_fields_ok=False),
            [
                "Huawei meter block not detected",
                "One or more required Huawei energy fields did not respond",
            ],
        )
        self.assertEqual(probe_huawei_mod._recommended_huawei_unit_template("profile"), None)
        self.assertTrue(probe_huawei_mod._recommended_huawei_ma_profile(SimpleNamespace(platform="MA"), "profile"))
        self.assertEqual(
            probe_huawei_mod._recommendation_location_text({"port": 502, "unit_id": 1}),
            "host=unknown, port=502, unit=1",
        )
        self.assertEqual(probe_huawei_mod._recommendation_hint_values({}), ("unknown", "unknown", "unknown"))
        self.assertIsNone(probe_huawei_mod._optional_int(True))
        self.assertIsNone(probe_huawei_mod._optional_int("bad"))
        self.assertEqual(probe_huawei_mod._optional_int("7"), 7)


class BranchCoverageNextClusterThreeRecommendationSchemaCases(unittest.TestCase):
    def test_recommendation_manifest_helpers_cover_validation_errors_and_normalization(self) -> None:
        manifest = recommendation_schema_mod.recommendation_bundle_manifest(
            source_id="hybrid",
            profile="huawei",
            config_path="/data/etc/huawei.ini",
            written_files={
                "config_snippet": "/tmp/snippet.txt",
                "wizard_hint": "/tmp/hint.txt",
                "summary": "/tmp/summary.txt",
            },
        )
        self.assertEqual(manifest["schema_type"], recommendation_schema_mod.RECOMMENDATION_BUNDLE_SCHEMA_TYPE)
        self.assertTrue(
            str(recommendation_schema_mod.recommendation_bundle_manifest_path("/tmp/bundle")).endswith(".manifest.json")
        )
        self.assertEqual(recommendation_schema_mod._manifest_string({"x": "  a "}, "x"), "a")
        self.assertEqual(recommendation_schema_mod._manifest_files({"files": {"x": "y"}}), {"x": "y"})
        self.assertEqual(
            recommendation_schema_mod._normalized_manifest_files(
                {"config_snippet": " a ", "wizard_hint": " b ", "summary": " c "}
            ),
            {"config_snippet": "a", "wizard_hint": "b", "summary": "c"},
        )
        self.assertEqual(
            recommendation_schema_mod.validate_recommendation_bundle_manifest(manifest),
            {
                "schema_type": recommendation_schema_mod.RECOMMENDATION_BUNDLE_SCHEMA_TYPE,
                "schema_version": recommendation_schema_mod.RECOMMENDATION_BUNDLE_SCHEMA_VERSION,
                "source_id": "hybrid",
                "profile": "huawei",
                "config_path": "/data/etc/huawei.ini",
                "files": {
                    "config_snippet": "/tmp/snippet.txt",
                    "wizard_hint": "/tmp/hint.txt",
                    "summary": "/tmp/summary.txt",
                },
            },
        )

        with self.assertRaisesRegex(ValueError, "missing source_id"):
            recommendation_schema_mod._manifest_required_string({}, "source_id")
        with self.assertRaisesRegex(ValueError, "missing files$"):
            recommendation_schema_mod._manifest_files({})
        with self.assertRaisesRegex(ValueError, "missing files.summary"):
            recommendation_schema_mod._normalized_manifest_files(
                {"config_snippet": "a", "wizard_hint": "b", "summary": ""}
            )
        with self.assertRaisesRegex(ValueError, "Unsupported recommendation bundle schema type"):
            recommendation_schema_mod.validate_recommendation_bundle_manifest({**manifest, "schema_type": "wrong"})
        with self.assertRaisesRegex(ValueError, "Unsupported recommendation bundle schema version"):
            recommendation_schema_mod.validate_recommendation_bundle_manifest({**manifest, "schema_version": 999})
