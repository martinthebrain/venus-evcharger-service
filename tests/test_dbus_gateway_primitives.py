# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import venus_evcharger.dbus_gateway_client as gateway_client_module
import venus_evcharger.dbus_gateway_core as gateway_core_module
import venus_evcharger.dbus_gateway_latency as gateway_latency_module
from venus_evcharger.dbus_gateway import (
    VENUS_EV_CHARGER_WRITABLE_PATHS,
    missing_required_venus_paths,
    mismatched_venus_writeability,
    venus_path_writeable,
)
from venus_evcharger import dbus_gateway, dbus_gateway_surface
from venus_evcharger import dbus_gateway_cache, dbus_gateway_commands
from venus_evcharger.dbus_gateway import (
    CacheValueMetadata,
    BATTERY_SOC_READ_KEY,
    DbusCacheStore,
    DbusCommandInbox,
    EVCS_FIELD_TO_PATH,
    EVCS_PATH_TO_FIELD,
    GRID_POWER_READ_KEY,
    GatewayClient,
    GatewayDbusServiceProxy,
    LatencyWindow,
    PV_POWER_READ_KEY,
    command_allowed_by_backpressure,
    command_queue_class,
    dbus_path_key,
    evcs_fields_to_paths,
    evcs_path_to_field,
    gateway_paths,
    gateway_read_value,
    gateway_value,
    read_json_file,
    require_gateway_read_key,
    write_json_file,
)


class DbusGatewayPrimitiveTests(unittest.TestCase):
    def test_venus_surface_contract_reports_missing_paths_and_writeability_mismatches(self) -> None:
        registered = {
            "/Mgmt/ProcessName",
            "/Mgmt/ProcessVersion",
            "/Mgmt/Connection",
            "/DeviceInstance",
            "/ProductId",
            "/ProductName",
            "/CustomName",
            "/FirmwareVersion",
            "/HardwareVersion",
            "/Serial",
            "/Connected",
            "/Position",
            "/UpdateIndex",
            "/Ac/Power",
            "/Ac/Current",
            "/Ac/Voltage",
            "/Ac/Energy/Forward",
            "/Session/Energy",
            "/Session/Time",
            "/Status",
            "/Auto/Health",
            "/Auto/State",
            "/Auto/StatusSource",
            "/Mode",
            "/StartStop",
            "/Enable",
            "/SetCurrent",
            "/AutoStart",
        }
        self.assertEqual(missing_required_venus_paths(registered), ())
        self.assertEqual(missing_required_venus_paths(registered - {"/Mode"}), ("/Mode",))
        self.assertTrue(mismatched_venus_writeability("/Mode", False))
        self.assertFalse(mismatched_venus_writeability("/Mode", True))
        self.assertTrue(mismatched_venus_writeability("/Ac/Power", True))
        self.assertFalse(mismatched_venus_writeability("/Ac/Power", False))
        self.assertTrue(venus_path_writeable("/Mode"))
        self.assertFalse(venus_path_writeable("/Ac/Power"))
        self.assertIn("/Auto/LearnChargePowerWindowSeconds", VENUS_EV_CHARGER_WRITABLE_PATHS)
        self.assertEqual(
            evcs_fields_to_paths({"ac_power_w": 1200.0, "session_time_s": 30, "unknown": "ignored"}),
            {"/Ac/Power": 1200.0, "/Session/Time": 30},
        )

    def test_evcs_semantic_field_contract_maps_gateway_surface_paths(self) -> None:
        expected_fields = {
            "update_index": "/UpdateIndex",
            "connected": "/Connected",
            "status": "/Status",
            "mode": "/Mode",
            "auto_start": "/AutoStart",
            "start_stop": "/StartStop",
            "enable": "/Enable",
            "set_current": "/SetCurrent",
            "min_current": "/MinCurrent",
            "max_current": "/MaxCurrent",
            "phase_selection": "/PhaseSelection",
            "phase_selection_active": "/PhaseSelectionActive",
            "supported_phase_selections": "/SupportedPhaseSelections",
            "auto_start_surplus_watts": "/Auto/StartSurplusWatts",
            "auto_dbus_backoff_base_seconds": "/Auto/DbusBackoffBaseSeconds",
            "auto_grid_recovery_start_seconds": "/Auto/GridRecoveryStartSeconds",
            "auto_learn_charge_power_window_seconds": "/Auto/LearnChargePowerWindowSeconds",
            "auto_phase_mismatch_lockout_seconds": "/Auto/PhaseMismatchLockoutSeconds",
            "auto_phase_lockout_reset": "/Auto/PhaseLockoutReset",
            "auto_contactor_lockout_reset": "/Auto/ContactorLockoutReset",
            "auto_software_update_run": "/Auto/SoftwareUpdateRun",
            "auto_software_update_available_version": "/Auto/SoftwareUpdateAvailableVersion",
            "auto_dbus_introspection_snapshot_age": "/Auto/DbusIntrospectionSnapshotAge",
            "auto_pv_read_errors": "/Auto/PvReadErrors",
            "auto_shelly_consecutive_errors": "/Auto/ShellyConsecutiveErrors",
        }

        for field, path in expected_fields.items():
            with self.subTest(field=field):
                self.assertEqual(EVCS_FIELD_TO_PATH[field], path)
                self.assertEqual(EVCS_PATH_TO_FIELD[path], field)
                self.assertEqual(evcs_path_to_field(path), field)

        self.assertEqual(evcs_path_to_field("/Not/Registered"), "")
        self.assertEqual(len(EVCS_FIELD_TO_PATH), len(set(EVCS_FIELD_TO_PATH)))
        self.assertEqual(len(EVCS_FIELD_TO_PATH), len(set(EVCS_FIELD_TO_PATH.values())))

    def test_evcs_generated_field_name_contract_handles_camel_case_paths(self) -> None:
        self.assertEqual(dbus_gateway_surface._snake_case("DbusBackoffBaseSeconds"), "dbus_backoff_base_seconds")
        self.assertEqual(dbus_gateway_surface._snake_case("PvReadErrors"), "pv_read_errors")
        self.assertEqual(dbus_gateway_surface._snake_case("SoftwareUpdateRun"), "software_update_run")
        self.assertEqual(dbus_gateway_surface._snake_case(" Bad-Name.Path "), "bad_name_path")
        self.assertEqual(dbus_gateway_surface._snake_case("XBadX"), "x_bad_x")
        self.assertEqual(
            dbus_gateway_surface._snake_case("DbusIntrospectionSnapshotAge"),
            "dbus_introspection_snapshot_age",
        )
        self.assertEqual(
            dbus_gateway_surface._field_name_from_venus_path("/Auto/DbusIntrospectionSnapshotAge"),
            "auto_dbus_introspection_snapshot_age",
        )
        self.assertEqual(
            dbus_gateway_surface._field_name_from_venus_path("///Auto//PvReadErrors//"),
            "auto_pv_read_errors",
        )

    def test_gateway_core_paths_priority_json_and_read_edges(self) -> None:
        with patch.dict(
            gateway_core_module.os.environ,
            {"VENUS_EVCHARGER_GATEWAY_RUN_DIR": " /tmp/env-gateway "},
        ):
            env_paths = gateway_paths()
        self.assertEqual(env_paths.run_dir, "/tmp/env-gateway")
        self.assertEqual(env_paths.socket_path, "/tmp/env-gateway/gateway.sock")
        self.assertEqual(env_paths.cache_path, "/tmp/env-gateway/dbus-cache.json")
        self.assertEqual(env_paths.cache_sequence_path, "/tmp/env-gateway/dbus-cache.seq")
        self.assertEqual(env_paths.health_path, "/tmp/env-gateway/dbus-health.json")
        self.assertEqual(env_paths.command_dir, "/tmp/env-gateway/dbus-commands")
        self.assertEqual(env_paths.core_command_dir, "/tmp/env-gateway/core-commands")

        explicit_paths = gateway_paths(" /tmp/explicit-gateway ")
        self.assertEqual(explicit_paths.run_dir, "/tmp/explicit-gateway")
        self.assertEqual(dbus_path_key("svc", "/Path"), "path:svc/Path")

        self.assertEqual(gateway_core_module.priority_rank(" safety "), 0)
        self.assertEqual(gateway_core_module.priority_rank("USER"), 1)
        self.assertEqual(gateway_core_module.priority_rank("unknown"), gateway_core_module.PRIORITY_VALUES["diagnostic"])
        self.assertEqual(gateway_core_module.priority_rank(None), gateway_core_module.PRIORITY_VALUES["diagnostic"])

        payload = gateway_core_module._json_ready({"nested": (object(), {"k": True}), 4: None})
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertEqual(payload["4"], None)
        self.assertTrue(payload["nested"][1]["k"])
        self.assertIn("object object", payload["nested"][0])
        self.assertTrue(gateway_core_module._is_json_scalar(False))
        self.assertFalse(gateway_core_module._is_json_scalar(object()))

        with tempfile.TemporaryDirectory() as temp_dir:
            default = {"fallback": True}
            missing = Path(temp_dir) / "missing.json"
            self.assertIs(read_json_file(str(missing), default), default)

            list_path = Path(temp_dir) / "list.json"
            list_path.write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(read_json_file(str(list_path), default), [1, 2])

            object_path = Path(temp_dir) / "object.json"
            object_path.write_text('{"ok": true}', encoding="utf-8")
            self.assertEqual(read_json_file(str(object_path), default), {"ok": True})

            broken = Path(temp_dir) / "broken.json"
            broken.write_text("{", encoding="utf-8")
            self.assertIs(read_json_file(str(broken), default), default)

            handle = MagicMock()
            opened = object()
            handle.__enter__.return_value = opened
            with patch("builtins.open", MagicMock(return_value=handle)) as open_mock, patch.object(
                gateway_core_module.json,
                "load",
                return_value={"opened": True},
            ) as json_load:
                self.assertEqual(read_json_file("explicit-encoding.json", default), {"opened": True})
            open_mock.assert_called_once_with("explicit-encoding.json", encoding="utf-8")
            json_load.assert_called_once_with(opened)

        class BadFloat:
            def __float__(self) -> float:
                raise ValueError("bad")

        self.assertEqual(gateway_core_module.float_or_default("2.5", 0.0), 2.5)
        self.assertEqual(gateway_core_module.float_or_default(BadFloat(), 4.0), 4.0)
        self.assertEqual(gateway_core_module.float_or_default(object(), 4.0), 4.0)

    def test_gateway_read_key_contract_is_semantic_and_strict(self) -> None:
        self.assertEqual(require_gateway_read_key(" grid_power_w "), GRID_POWER_READ_KEY)
        self.assertEqual(require_gateway_read_key(" pv_power_w "), PV_POWER_READ_KEY)
        self.assertEqual(require_gateway_read_key(" battery_soc "), BATTERY_SOC_READ_KEY)
        self.assertEqual(
            {GRID_POWER_READ_KEY, PV_POWER_READ_KEY, BATTERY_SOC_READ_KEY},
            set(gateway_core_module.FAST_READ_KEYS),
        )
        for invalid_key in ("", None, "path:svc/Raw", "com.victronenergy.system:/Dc/Pv/Power"):
            with self.subTest(invalid_key=invalid_key):
                with self.assertRaisesRegex(ValueError, "Unsupported gateway read key"):
                    require_gateway_read_key(invalid_key)

        snapshot = {
            "values": {
                GRID_POWER_READ_KEY: {"value": -20.0, "status": "fresh", "age_s": 0.5},
                "path:svc/Raw": {"value": 12.0, "status": "fresh", "age_s": 0.5},
            }
        }
        self.assertEqual(gateway_read_value(snapshot, GRID_POWER_READ_KEY, max_age_seconds=5.0), -20.0)
        self.assertEqual(gateway_value(snapshot, "path:svc/Raw", max_age_seconds=5.0), 12.0)
        with self.assertRaisesRegex(ValueError, "Unsupported gateway read key"):
            gateway_read_value(snapshot, "path:svc/Raw", max_age_seconds=5.0)

    def test_json_helpers_cache_snapshot_and_load_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            store = DbusCacheStore(paths, stale_after_seconds=1.0)

            store.update_value("nested", {"tuple": (object(),)}, source="svc/path", now=10.0)
            store.update_value(
                "metadata",
                2,
                metadata=CacheValueMetadata(source="svc/metadata", status="cached", confidence=0.7, now=9.0),
            )
            store.update_value(
                "metadata",
                3,
                metadata=CacheValueMetadata(source="old/source", now=9.5),
                source="new/source",
                confidence=0.8,
            )
            fresh = store.snapshot(now=10.5)
            stale = store.snapshot(now=12.0)
            self.assertEqual(fresh["values"]["nested"]["status"], "fresh")
            self.assertEqual(stale["values"]["nested"]["status"], "stale")
            self.assertEqual(fresh["values"]["metadata"]["source"], "new/source")
            self.assertEqual(fresh["values"]["metadata"]["confidence"], 0.8)
            self.assertIn("object object", stale["values"]["nested"]["value"]["tuple"][0])

            store.mark_error("nested", source="svc/path", error="bad", now=13.0)
            error_entry = store.snapshot(now=13.0)["values"]["nested"]
            self.assertEqual(error_entry["status"], "error")
            self.assertEqual(error_entry["error_at"], 13.0)
            store.update_services(["svc.a"], now=14.0)
            store.write_snapshot_files()

            loaded = DbusCacheStore.load_snapshot(paths.cache_path, now=14.0)
            self.assertEqual(loaded["sequence"], store.sequence)
            self.assertEqual(
                DbusCacheStore.load_snapshot(
                    paths.cache_path,
                    max_age_seconds=0.1,
                    now=float(loaded["captured_at"]) + 1.0,
                ),
                {},
            )
            invalid_time = Path(temp_dir) / "invalid-time.json"
            invalid_time.write_text(json.dumps({"captured_at": 0.0}), encoding="utf-8")
            self.assertEqual(DbusCacheStore.load_snapshot(str(invalid_time), now=14.0), {})
            invalid_time.write_text(json.dumps({"captured_at": "bad"}), encoding="utf-8")
            self.assertEqual(DbusCacheStore.load_snapshot(str(invalid_time), now=14.0), {})
            self.assertEqual(DbusCacheStore.load_snapshot(str(Path(temp_dir) / "missing.json")), {})

            invalid = Path(temp_dir) / "invalid.json"
            invalid.write_text("[]", encoding="utf-8")
            self.assertEqual(DbusCacheStore.load_snapshot(str(invalid)), {})
            no_values = {"captured_at": 14.0, "values": []}
            self.assertIsNone(DbusCacheStore.value_entry(no_values, "nested"))
            self.assertIsNone(DbusCacheStore.value_entry({"values": {"nested": 1}}, "nested"))
            copied_entry = DbusCacheStore.value_entry({"values": {"nested": {"value": 1}}}, "nested")
            self.assertEqual(copied_entry, {"value": 1})
            assert copied_entry is not None
            copied_entry["value"] = 2
            self.assertEqual(DbusCacheStore.value_entry({"values": {"nested": {"value": 1}}}, "nested"), {"value": 1})

            broken = Path(temp_dir) / "broken.json"
            broken.write_text("{", encoding="utf-8")
            self.assertEqual(read_json_file(str(broken), {"fallback": True}), {"fallback": True})
            out = Path(temp_dir) / "out.json"
            write_json_file(str(out), {"value": object()})
            self.assertIn("object object", read_json_file(str(out), {})["value"])

    def test_cache_helpers_cover_freshness_metadata_and_error_edges(self) -> None:
        self.assertEqual(dbus_gateway_cache._value_age(0.0, 100.0), 0.0)
        self.assertEqual(dbus_gateway_cache._value_age(120.0, 100.0), 0.0)
        self.assertFalse(dbus_gateway_cache._value_is_stale("error", 20.0, 1.0))
        self.assertFalse(dbus_gateway_cache._value_is_stale("fresh", 20.0, 0.0))
        self.assertFalse(dbus_gateway_cache._value_is_stale("fresh", 1.0, 1.0))
        self.assertTrue(dbus_gateway_cache._value_is_stale("fresh", 20.0, 1.0))
        self.assertTrue(dbus_gateway_cache._valid_snapshot_payload({"captured_at": 1.0}))
        self.assertFalse(dbus_gateway_cache._valid_snapshot_payload({"captured_at": 0.0}))
        self.assertFalse(dbus_gateway_cache._valid_snapshot_payload({"captured_at": object()}))
        self.assertFalse(dbus_gateway_cache._valid_snapshot_payload([]))
        valid_payload = {"captured_at": 1.0, "values": {}}
        self.assertIs(dbus_gateway_cache._snapshot_payload(valid_payload), valid_payload)
        self.assertFalse(dbus_gateway_cache._snapshot_too_old(100.0, 100.0, 0.0))
        self.assertTrue(dbus_gateway_cache._snapshot_too_old(100.0, 100.1, 0.0))
        self.assertFalse(dbus_gateway_cache._snapshot_too_old(99.0, 100.0, 1.0))
        self.assertFalse(dbus_gateway_cache._snapshot_too_old(1.0, 100.0, -1.0))
        self.assertTrue(dbus_gateway_cache._snapshot_too_old(1.0, 100.0, 1.0))

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            self.assertEqual(DbusCacheStore(paths).stale_after_seconds, 10.0)
            store = DbusCacheStore(paths, stale_after_seconds=-1)
            self.assertIs(store.paths, paths)
            self.assertEqual(store.stale_after_seconds, 0.0)
            self.assertEqual(store.sequence, 0)
            self.assertEqual(store.values, {})
            self.assertEqual(store.services, {})
            self.assertEqual(
                store.health,
                {
                    "state": "init",
                    "degraded_until": 0.0,
                    "timeouts_60s": 0,
                    "avg_latency_ms": 0.0,
                    "max_latency_ms": 0.0,
                },
            )
            with patch.object(dbus_gateway_cache, "_now", return_value=42.0):
                store.update_value("defaulted", 5)
            self.assertEqual(store.values["defaulted"]["age_s"], 0.0)
            entry = store.snapshot(now=50.0)["values"]["defaulted"]
            self.assertEqual(store.sequence, 1)
            self.assertEqual(entry["value"], 5)
            self.assertEqual(entry["source"], "")
            self.assertEqual(entry["status"], "fresh")
            self.assertEqual(entry["confidence"], 1.0)
            self.assertEqual(entry["last_error"], "")
            self.assertEqual(entry["updated_at"], 42.0)
            self.assertEqual(entry["age_s"], 8.0)

            merged = dbus_gateway_cache._cache_value_metadata(
                CacheValueMetadata(source="old", status="cached", confidence=0.4, last_error="old-error", now=7.0),
                {"source": "new", "status": "fresh", "confidence": "0.9", "last_error": "new-error", "now": "8.5"},
            )
            self.assertEqual(
                merged,
                CacheValueMetadata(
                    source="new",
                    status="fresh",
                    confidence=0.9,
                    last_error="new-error",
                    now=8.5,
                ),
            )
            metadata_fallback = dbus_gateway_cache._cache_value_metadata(
                CacheValueMetadata(source="old", status="cached", confidence=0.4, last_error="old-error", now=7.0),
                {"source": "new"},
            )
            self.assertEqual(
                metadata_fallback,
                CacheValueMetadata(source="new", status="cached", confidence=0.4, last_error="old-error", now=7.0),
            )
            field_only = dbus_gateway_cache._cache_value_metadata(
                None,
                {"source": "field", "status": "cached", "confidence": b"0.6", "last_error": "warn", "now": bytearray(b"9")},
            )
            self.assertEqual(
                field_only,
                CacheValueMetadata(source="field", status="cached", confidence=0.6, last_error="warn", now=9.0),
            )

            store.update_value(
                "metadata-fallback",
                6,
                metadata=CacheValueMetadata(source="svc/fallback", confidence=0.55, now=88.0),
                confidence=object(),
                now=object(),
            )
            fallback_entry = store.snapshot(now=90.0)["values"]["metadata-fallback"]
            self.assertEqual(store.sequence, 2)
            self.assertEqual(fallback_entry["confidence"], 0.55)
            self.assertEqual(fallback_entry["updated_at"], 88.0)
            self.assertEqual(fallback_entry["source"], "svc/fallback")

            store.mark_error("missing", source="svc/path", error=RuntimeError("boom"), now=60.0)
            self.assertEqual(store.values["missing"]["age_s"], 60.0)
            error_entry = store.snapshot(now=60.0)["values"]["missing"]
            self.assertEqual(store.sequence, 3)
            self.assertIsNone(error_entry["value"])
            self.assertEqual(error_entry["source"], "svc/path")
            self.assertEqual(error_entry["updated_at"], 0.0)
            self.assertEqual(error_entry["error_at"], 60.0)
            self.assertEqual(error_entry["age_s"], 0.0)
            self.assertEqual(error_entry["status"], "error")
            self.assertEqual(error_entry["last_error"], "boom")
            self.assertEqual(error_entry["confidence"], 0.0)

            store.update_value(123, {"x": object()}, source="svc/value", status="cached", confidence=0.25, last_error="soft", now=70.0)
            store.mark_error(123, source="svc/error", error="failed", now=75.5)
            self.assertEqual(store.sequence, 5)
            self.assertEqual(store.values["123"]["age_s"], 5.5)
            numeric_error = store.snapshot(now=76.0)["values"]["123"]
            self.assertEqual(numeric_error["value"]["x"].split()[0], "<object")
            self.assertEqual(numeric_error["source"], "svc/error")
            self.assertEqual(numeric_error["updated_at"], 70.0)
            self.assertEqual(numeric_error["error_at"], 75.5)
            self.assertEqual(numeric_error["age_s"], 6.0)
            self.assertEqual(numeric_error["last_error"], "failed")

            store.update_value("instant-error", 1, source="svc/value", now=77.0)
            store.mark_error("instant-error", source="svc/error", error="same-tick", now=77.0)
            self.assertEqual(store.values["instant-error"]["age_s"], 0.0)

            store.update_services(["svc.a", 42], now=80.0)
            self.assertEqual(store.sequence, 8)
            self.assertEqual(
                store.services,
                {
                    "svc.a": {"seen_at": 80.0, "status": "present"},
                    "42": {"seen_at": 80.0, "status": "present"},
                },
            )
            snapshot = store.snapshot(now=81.0)
            self.assertEqual(snapshot["schema_version"], dbus_gateway_cache.DBUS_GATEWAY_SCHEMA_VERSION)
            self.assertEqual(snapshot["sequence"], store.sequence)
            self.assertEqual(snapshot["captured_at"], 81.0)
            self.assertEqual(snapshot["dbus_health"], store.health)
            self.assertEqual(snapshot["services"], store.services)
            self.assertEqual(store.value_snapshot({"updated_at": 0.0}, 82.0)["status"], "unknown")
            self.assertEqual(store.value_snapshot({"updated_at": 80.0, "status": "cached"}, 82.0)["status"], "cached")

            store.write_snapshot_files()
            self.assertEqual(Path(paths.cache_sequence_path).read_text(encoding="utf-8"), f"{store.sequence}\n")
            health = read_json_file(paths.health_path, {})
            self.assertEqual(
                health,
                {
                    "schema_version": dbus_gateway_cache.DBUS_GATEWAY_SCHEMA_VERSION,
                    "sequence": store.sequence,
                    "captured_at": health["captured_at"],
                    "dbus_health": store.health,
                },
            )
            self.assertEqual(DbusCacheStore.load_snapshot(paths.cache_path, max_age_seconds=-1.0, now=99999.0)["sequence"], store.sequence)
            default_age_path = Path(temp_dir) / "default-age.json"
            default_age_path.write_text(json.dumps({"captured_at": 1.0}), encoding="utf-8")
            self.assertEqual(DbusCacheStore.load_snapshot(str(default_age_path), now=31.5), {})

    def test_command_inbox_coalesce_ordering_and_error_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"kind": "set_value", "created_at": 1.0, "priority": "diagnostic", "coalesce_key": "k"})
            second = inbox.enqueue({"kind": "set_value", "created_at": 2.0, "priority": "diagnostic", "coalesce_key": "k"})
            self.assertEqual(first, second)
            pending_set_value = inbox.load_pending()[0][1]
            self.assertEqual(pending_set_value["id"], Path(first).stem)
            self.assertEqual(pending_set_value["created_at"], 1.0)
            self.assertEqual(pending_set_value["queue_class"], "remote-write")
            self.assertEqual(pending_set_value["lifecycle_state"], "coalesced")
            self.assertGreaterEqual(pending_set_value["updated_at"], 2.0)
            desired_path = inbox.enqueue(
                {
                    "kind": "publish_desired",
                    "created_at": 2.0,
                    "priority": "publish",
                    "coalesce_key": "publish:desired",
                    "paths": {"/Connected": 1, "/Status": 1, "/Mode": 0},
                }
            )
            self.assertEqual(
                inbox.enqueue(
                    {
                        "kind": "publish_desired",
                        "created_at": 3.0,
                        "priority": "publish",
                        "coalesce_key": "publish:desired",
                        "paths": {"/Ac/Power": 1200.0, "/Mode": 1},
                    }
                ),
                desired_path,
            )
            pending_desired = read_json_file(desired_path, {})
            self.assertEqual(
                pending_desired["paths"],
                {"/Connected": 1, "/Status": 1, "/Mode": 1, "/Ac/Power": 1200.0},
            )
            services_first = inbox.enqueue({"kind": "refresh_services", "created_at": 3.0})
            services_second = inbox.enqueue({"kind": "refresh_services", "created_at": 4.0})
            self.assertEqual(services_first, services_second)
            pending_refresh = [
                command
                for _path, command in inbox.load_pending()
                if command.get("kind") == "refresh_services"
            ][0]
            self.assertEqual(pending_refresh["coalesce_key"], "refresh:services")
            legacy_refresh = Path(temp_dir) / "commands" / "legacy-refresh.json"
            legacy_refresh.write_text(
                '{"kind":"refresh_services","created_at":2.0,"coalesce_key":"refresh-services"}',
                encoding="utf-8",
            )
            legacy_loaded = [
                command
                for _path, command in inbox.load_pending()
                if command.get("kind") == "refresh_services" and command.get("created_at") == 2.0
            ][0]
            self.assertEqual(legacy_loaded["coalesce_key"], "refresh:services")
            no_key_path = inbox.enqueue({"kind": "set_value", "created_at": 5.0})
            literal_x_path = inbox.enqueue({"kind": "set_value", "created_at": 6.0, "coalesce_key": "XXXX"})
            self.assertEqual(inbox.remove_coalesced(""), 0)
            self.assertTrue(Path(literal_x_path).exists())
            self.assertEqual(inbox.remove_coalesced("XXXX"), 1)
            self.assertTrue(Path(no_key_path).exists())
            self.assertEqual(inbox.remove_coalesced("refresh:services"), 2)
            self.assertFalse(any(command.get("kind") == "refresh_services" for _path, command in inbox.load_pending()))

            commands = [
                ("a", {"id": "a", "created_at": "bad", "priority": "diagnostic"}),
                ("old", {"id": "old", "created_at": 10.0, "priority": "diagnostic", "coalesce_key": "same"}),
                ("newer-lower", {"id": "newer-lower", "created_at": 9.0, "priority": "safety", "coalesce_key": "same"}),
                ("kept-old", {"id": "kept-old", "created_at": 20.0, "priority": "safety", "coalesce_key": "same"}),
            ]
            coalesced = DbusCommandInbox.coalesce(commands)
            self.assertEqual([item[0] for item in coalesced], ["kept-old", "a"])

            class Floaty:
                def __float__(self) -> float:
                    return 3.0

            class BadFloaty:
                def __float__(self) -> float:
                    raise TypeError("bad")

            ordered = DbusCommandInbox.coalesce(
                [
                    ("floaty", {"id": "floaty", "created_at": Floaty(), "priority": "read"}),
                    ("bad", {"id": "bad", "created_at": BadFloaty(), "priority": "read"}),
                    ("none", {"id": "none", "created_at": object(), "priority": "read"}),
                ]
            )
            self.assertEqual([item[0] for item in ordered], ["bad", "none", "floaty"])

            Path(inbox.command_dir, "bad.json").write_text("{", encoding="utf-8")
            Path(inbox.command_dir, "list.json").write_text("[]", encoding="utf-8")
            self.assertTrue(inbox.load_pending())
            inbox.remove(str(Path(inbox.command_dir) / "missing.json"))
            with patch.object(dbus_gateway.Path, "glob", side_effect=OSError("boom")):
                self.assertEqual(inbox.load_pending(), [])
            self.assertTrue(DbusCommandInbox._should_replace_existing(str(Path(inbox.command_dir) / "bad.json"), {}))
            self.assertTrue(DbusCommandInbox._should_replace_existing(str(Path(inbox.command_dir) / "list.json"), {}))
            weird_existing = inbox.enqueue({"kind": "set_value", "value": 1, "coalesce_key": "weird"})
            Path(weird_existing).write_text("[]", encoding="utf-8")
            self.assertEqual(inbox.enqueue({"kind": "set_value", "value": 2, "coalesce_key": "weird"}), weird_existing)
            self.assertEqual(read_json_file(weird_existing, {})["value"], 2)

            keep_old = DbusCommandInbox.coalesce(
                [
                    ("old", {"id": "old", "created_at": 10.0, "priority": "safety", "coalesce_key": "k"}),
                    ("new", {"id": "new", "created_at": 11.0, "priority": "diagnostic", "coalesce_key": "k"}),
                ]
            )
            self.assertEqual(keep_old[0][0], "old")

    def test_command_inbox_private_helpers_cover_priority_and_publish_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            queued_path = inbox.enqueue({"kind": "set_value", "service": "svc", "path": "/Mode", "value": 1})
            queued_payload = read_json_file(queued_path, {})
            self.assertEqual(queued_payload["lifecycle_state"], "queued")
            self.assertIn("created_at", queued_payload)
            self.assertIn("queue_class", queued_payload)

        payload = DbusCommandInbox._new_payload(
            "cmd-1",
            {"kind": "publish_value", "priority": "publish", "path": "/Mode", "created_at": 11.0},
        )
        self.assertEqual(payload["schema_version"], dbus_gateway_commands.DBUS_GATEWAY_SCHEMA_VERSION)
        self.assertEqual(payload["id"], "cmd-1")
        self.assertEqual(payload["created_at"], 11.0)
        self.assertEqual(payload["queue_class"], "gui-critical-publish")
        self.assertEqual(
            set(payload),
            {"schema_version", "id", "created_at", "kind", "priority", "path", "queue_class"},
        )
        with patch.object(dbus_gateway_commands, "_now", return_value=44.0):
            self.assertEqual(DbusCommandInbox._new_payload("cmd-2", {"kind": "set_value"})["created_at"], 44.0)
            self.assertEqual(
                DbusCommandInbox._new_payload("cmd-3", {"kind": "set_value", "created_at": "bad"})["created_at"],
                44.0,
            )
        self.assertEqual(
            DbusCommandInbox._command_id({"coalesce_key": "ev:/Mode"}),
            DbusCommandInbox._command_id({"coalesce_key": "ev:/Mode"}),
        )
        coalesced_id = DbusCommandInbox._command_id({"coalesce_key": "ev:/Mode"})
        random_id = DbusCommandInbox._command_id({})
        self.assertTrue(coalesced_id.startswith("coalesced-"))
        self.assertEqual(len(coalesced_id.removeprefix("coalesced-")), 24)
        self.assertTrue(random_id.startswith("cmd-"))
        self.assertEqual(len(random_id.rsplit("-", 1)[1]), 8)
        self.assertEqual(
            DbusCommandInbox._normalized_command({"type": "refresh_services"})["coalesce_key"],
            "refresh:services",
        )

        self.assertTrue(
            DbusCommandInbox._should_replace_existing_payload(
                {"priority": "diagnostic", "created_at": 10.0},
                {"priority": "user", "created_at": 1.0},
            )
        )
        self.assertFalse(
            DbusCommandInbox._should_replace_existing_payload(
                {"priority": "safety", "created_at": 10.0},
                {"priority": "diagnostic", "created_at": 20.0},
            )
        )
        self.assertFalse(
            DbusCommandInbox._should_replace_existing_payload(
                {"priority": "normal", "created_at": 10.0},
                {"priority": "normal", "created_at": 9.0},
            )
        )
        self.assertTrue(
            DbusCommandInbox._should_replace_existing_payload(
                {"priority": "normal", "created_at": 10.0},
                {"priority": "normal", "created_at": 10.0},
            )
        )
        self.assertEqual(
            dbus_gateway_commands._selected_coalesced_command(
                ("old", {"priority": "safety", "created_at": 20.0}),
                ("new", {"priority": "diagnostic", "created_at": 30.0}),
            )[0],
            "old",
        )
        self.assertEqual(
            dbus_gateway_commands._selected_coalesced_command(
                ("old", {"priority": "diagnostic", "created_at": 20.0}),
                ("new", {"priority": "user", "created_at": 10.0}),
            )[0],
            "new",
        )
        self.assertEqual(
            dbus_gateway_commands._selected_coalesced_command(
                ("old", {"priority": "user", "created_at": 20.0}),
                ("new", {"priority": "user", "created_at": 21.0}),
            )[0],
            "new",
        )
        self.assertEqual(
            dbus_gateway_commands._selected_coalesced_command(
                ("old", {"priority": "user", "created_at": 20.0}),
                ("new", {"priority": "user", "created_at": 20.0}),
            )[0],
            "new",
        )
        self.assertEqual(
            dbus_gateway_commands._selected_coalesced_command(
                ("old", {"priority": "user", "created_at": 20.0}),
                ("new", {"priority": "user", "created_at": 19.0}),
            )[0],
            "old",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            target = str(Path(temp_dir) / "coalesced.json")
            Path(target).write_text("{}", encoding="utf-8")
            self.assertFalse(dbus_gateway_commands._coalesced_target_exists({}, target))
            self.assertTrue(dbus_gateway_commands._coalesced_target_exists({"coalesce_key": " k "}, target))

            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            Path(inbox.command_dir).mkdir(parents=True, exist_ok=True)
            missing_target = str(Path(temp_dir) / "commands" / "coalesced-missing.json")
            new_payload = {"priority": "user", "created_at": 12.0, "coalesce_key": "k"}
            self.assertEqual(inbox._merge_existing_coalesced_payload(new_payload, missing_target, new_payload), "write-new")
            Path(missing_target).write_text('{"priority":"safety","created_at":20.0}', encoding="utf-8")
            low_payload = {"priority": "diagnostic", "created_at": 30.0, "coalesce_key": "k"}
            self.assertEqual(inbox._merge_existing_coalesced_payload(low_payload, missing_target, low_payload), "keep-existing")
            self.assertFalse(DbusCommandInbox._should_replace_existing(missing_target, low_payload))
            high_payload = {"priority": "safety", "created_at": 31.0, "coalesce_key": "k"}
            self.assertEqual(inbox._merge_existing_coalesced_payload(high_payload, missing_target, high_payload), "write-new")
            self.assertEqual(high_payload["lifecycle_state"], "coalesced")

        self.assertTrue(dbus_gateway_commands._replace_existing_coalesced([], {}))
        self.assertTrue(
            dbus_gateway_commands._same_priority(
                {"priority": "publish"},
                {"priority": "publish"},
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_desired_payload(
                {"kind": "publish_desired", "priority": "publish"},
                {"kind": "publish_desired", "priority": "diagnostic"},
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_desired_payload(
                {"kind": "set_value", "priority": "publish"},
                {"kind": "publish_desired", "priority": "publish"},
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_fields_payload(
                [],
                {"kind": "publish_fields", "priority": "publish"},
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_fields_payload(
                {"kind": "publish_fields", "priority": "diagnostic"},
                {"kind": "publish_fields", "priority": "publish"},
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_fields_payload(
                {"kind": "publish_desired", "priority": "publish"},
                {"kind": "publish_fields", "priority": "publish"},
            )
        )
        self.assertTrue(
            dbus_gateway_commands._same_publish_fields_payload(
                {"kind": "publish_fields", "priority": "publish"},
                {"kind": "publish_fields", "priority": "publish"},
            )
        )
        one_sided_payload = {"kind": "publish_desired", "priority": "publish", "paths": {"/Mode": 1}}
        dbus_gateway_commands._merge_publish_desired_paths(
            {"kind": "publish_desired", "priority": "publish", "paths": []},
            one_sided_payload,
        )
        self.assertEqual(one_sided_payload["paths"], {"/Mode": 1})
        field_payload = {"kind": "publish_fields", "priority": "publish", "fields": {"mode": 1}}
        dbus_gateway_commands._merge_publish_fields(
            {"kind": "publish_fields", "priority": "publish", "fields": {"auto_start": 1}},
            field_payload,
        )
        self.assertEqual(field_payload["fields"], {"auto_start": 1, "mode": 1})
        one_sided_field_payload = {"kind": "publish_fields", "priority": "publish", "fields": {"mode": 1}}
        dbus_gateway_commands._merge_publish_fields(
            {"kind": "publish_fields", "priority": "publish", "fields": []},
            one_sided_field_payload,
        )
        self.assertEqual(one_sided_field_payload["fields"], {"mode": 1})
        mixed_publish_payload = {
            "kind": "publish_fields",
            "priority": "publish",
            "paths": {"/Mode": 1},
            "fields": {"mode": 1},
        }
        dbus_gateway_commands._merge_coalesced_publish_payload(
            {
                "kind": "publish_fields",
                "priority": "publish",
                "paths": {"/AutoStart": 0},
                "fields": {"auto_start": 0},
            },
            mixed_publish_payload,
        )
        self.assertEqual(mixed_publish_payload["paths"], {"/Mode": 1})
        self.assertEqual(mixed_publish_payload["fields"], {"auto_start": 0, "mode": 1})
        payload = {"created_at": 20.0}
        dbus_gateway_commands._mark_coalesced_payload([], payload)
        self.assertEqual(payload, {"created_at": 20.0, "lifecycle_state": "coalesced"})

        payload = {"priority": "publish", "created_at": 20.0}
        with patch.object(dbus_gateway_commands, "_now", return_value=30.0):
            dbus_gateway_commands._mark_coalesced_payload(
                {"priority": "publish", "created_at": 10.0},
                payload,
            )
        self.assertEqual(payload["created_at"], 10.0)
        self.assertEqual(payload["updated_at"], 30.0)
        payload = {"priority": "publish", "created_at": 22.0}
        dbus_gateway_commands._mark_coalesced_payload({"priority": "publish", "created_at": 0.0}, payload)
        self.assertEqual(payload["created_at"], 22.0)

        ordered = sorted(
            [
                {"kind": "register_path", "id": "path", "created_at": 2.0},
                {"kind": "register_service", "id": "service", "created_at": 3.0},
                {"kind": "publish_value", "priority": "publish", "path": "/Session/Time", "id": "time"},
                {"kind": "publish_value", "priority": "normal", "path": "/Session/Time", "id": "normal"},
                {"kind": "set_value", "id": "b", "created_at": 4.0},
                {"kind": "set_value", "id": "a", "created_at": 4.0},
            ],
            key=dbus_gateway_commands._command_order_key,
        )
        self.assertEqual([command["id"] for command in ordered], ["time", "normal", "service", "path", "a", "b"])
        self.assertEqual(dbus_gateway_commands._command_order_key({})[-1], "")
        self.assertEqual(dbus_gateway_commands._command_kind_rank({"kind": "register_service"}), 0)
        self.assertEqual(dbus_gateway_commands._command_kind_rank({"kind": "register_path"}), 1)
        self.assertEqual(dbus_gateway_commands._command_kind_rank({"type": "register_service"}), 0)
        self.assertEqual(dbus_gateway_commands._command_kind_rank({"type": "register_path"}), 1)
        self.assertEqual(dbus_gateway_commands._command_kind_rank({"kind": "set_value"}), 2)
        self.assertEqual(dbus_gateway_commands._publish_path_rank({"kind": "set_value", "path": "/Mode"}), 0)
        self.assertEqual(
            dbus_gateway_commands._publish_path_rank({"kind": "publish_value", "priority": "publish", "path": "/Mode"}),
            dbus_gateway_commands.PUBLISH_PATH_RANKS["/Mode"],
        )
        self.assertEqual(
            dbus_gateway_commands._publish_path_rank(
                {"kind": "publish_value", "priority": "publish", "path": "/Unranked"}
            ),
            3,
        )
        self.assertTrue(dbus_gateway_commands._ranked_publish_command({"kind": "publish_value", "priority": "publish"}))
        self.assertFalse(dbus_gateway_commands._ranked_publish_command({"kind": "publish_value", "priority": "user"}))
        self.assertFalse(dbus_gateway_commands._ranked_publish_command({"kind": "set_value", "priority": "publish"}))
        self.assertTrue(dbus_gateway_commands._publish_priority({"priority": " publish "}))
        self.assertFalse(dbus_gateway_commands._publish_priority({"priority": "user"}))
        self.assertEqual(dbus_gateway_commands._command_text({"priority": ""}, "priority"), "")
        self.assertEqual(dbus_gateway_commands._command_text({}, "priority"), "")
        self.assertEqual(dbus_gateway_commands._command_kind({"type": "set_value"}), "set_value")
        self.assertEqual(dbus_gateway_commands._command_kind({"kind": "publish_value", "type": "set_value"}), "publish_value")
        self.assertEqual(dbus_gateway_commands._command_kind({}), "")

    def test_gateway_client_transport_and_command_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            client = GatewayClient(paths, timeout_seconds=0.01)

            class FakeSocket:
                def __init__(self, response: bytes | BaseException) -> None:
                    self.response = response
                    self.sent = b""

                def __enter__(self) -> "FakeSocket":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def settimeout(self, _timeout: float) -> None:
                    return None

                def connect(self, _path: str) -> None:
                    if isinstance(self.response, BaseException):
                        raise self.response

                def sendall(self, data: bytes) -> None:
                    self.sent = data

                def recv(self, _size: int) -> bytes:
                    return self.response if isinstance(self.response, bytes) else b""

            for response, expected_ok in (
                (b"", True),
                (b'{"ok":true}', True),
                (b"[]", False),
                (RuntimeError("offline"), False),
            ):
                with patch.object(dbus_gateway.socket, "socket", return_value=FakeSocket(response)):
                    self.assertEqual(client.send({"value": object()})["ok"], expected_ok)

            client.publish_path("/Mode", 1)
            client.publish_paths({"/Ac/Power": 1200.0, "/Auto/Reason": "ok", "": "ignored"})
            client.publish_paths({"": "ignored"})
            client.publish_fields({"ac_power_w": 1200.0, "session_time_s": 30, "": "ignored"})
            client.publish_fields({"": "ignored"})
            client.register_path("/Mode", 1, writeable=True)
            client.request_read(GRID_POWER_READ_KEY)
            client.request_raw_value("svc", "/Path", reason="freshen")
            client.enqueue_command({"kind": "custom"})
            pending = DbusCommandInbox(paths.command_dir).load_pending()
            self.assertEqual(len(pending), 7)
            desired = [command for _path, command in pending if command.get("kind") == "publish_desired"][0]
            self.assertEqual(desired["paths"], {"/Ac/Power": 1200.0, "/Auto/Reason": "ok"})
            fields = [command for _path, command in pending if command.get("kind") == "publish_fields"][0]
            self.assertEqual(fields["fields"], {"ac_power_w": 1200.0, "session_time_s": 30})

            store = DbusCacheStore(paths)
            store.update_value("grid_power_w", 12.0, source="svc/path")
            store.health["backpressure"] = {"state": "slow"}
            store.write_snapshot_files()
            self.assertEqual(client.load_cache()["values"]["grid_power_w"]["value"], 12.0)
            self.assertEqual(client.load_health()["backpressure"]["state"], "slow")
            self.assertEqual(client.backpressure_state(), "slow")
            client.publish_path("/Auto/Reason", "optional")
            self.assertEqual(len(DbusCommandInbox(paths.command_dir).load_pending()), 7)
            client.publish_path("/Mode", 2, priority="user")
            pending = DbusCommandInbox(paths.command_dir).load_pending()
            self.assertEqual(len(pending), 7)
            mode = [
                command
                for _path, command in pending
                if command.get("path") == "/Mode" and command.get("kind") == "publish_value"
            ][0]
            self.assertEqual(mode["value"], 2)

    def test_gateway_client_contracts_for_transport_commands_and_backpressure_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            client = GatewayClient(paths, timeout_seconds=0.0)
            self.assertEqual(client.timeout_seconds, 0.05)
            default_client = GatewayClient(paths)
            self.assertEqual(default_client.timeout_seconds, 0.5)
            self.assertEqual(default_client.paths, paths)
            self.assertEqual(default_client.commands.command_dir, paths.command_dir)
            self.assertEqual(default_client._backpressure_cache, (0.0, "unknown"))

            class InspectableSocket:
                def __init__(self, response: bytes) -> None:
                    self.response = response
                    self.timeout: float | None = None
                    self.connected_path = ""
                    self.sent = b""

                def __enter__(self) -> "InspectableSocket":
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def settimeout(self, timeout: float) -> None:
                    self.timeout = timeout

                def connect(self, path: str) -> None:
                    self.connected_path = path

                def sendall(self, data: bytes) -> None:
                    self.sent = data

                def recv(self, size: int) -> bytes:
                    self.recv_size = size
                    return self.response

            sock = InspectableSocket(b'{"ok":true,"answer":42}')
            with patch.object(gateway_client_module.socket, "socket", return_value=sock) as socket_factory:
                response = client.send({"value": object()})
            socket_factory.assert_called_once_with(gateway_client_module.socket.AF_UNIX, gateway_client_module.socket.SOCK_STREAM)
            self.assertEqual(response, {"ok": True, "answer": 42})
            self.assertEqual(sock.timeout, 0.05)
            self.assertEqual(sock.connected_path, paths.socket_path)
            self.assertEqual(sock.recv_size, 65536)
            self.assertIn(b'"value"', sock.sent)
            self.assertIn(b"object object", sock.sent)
            self.assertTrue(sock.sent.endswith(b"\n"))
            self.assertEqual(sock.sent.count(b"\n"), 1)

            invalid_sock = InspectableSocket(b"[]")
            with patch.object(gateway_client_module.socket, "socket", return_value=invalid_sock):
                self.assertEqual(client.send({"request": "bad"}), {"ok": False, "error": "invalid-response"})

            class FailingSocket(InspectableSocket):
                def connect(self, path: str) -> None:
                    self.connected_path = path
                    raise RuntimeError("offline")

            failing_sock = FailingSocket(b"")
            with patch.object(gateway_client_module.socket, "socket", return_value=failing_sock):
                self.assertEqual(client.send({"request": "fail"}), {"ok": False, "error": "offline"})

            enqueuer = GatewayClient(paths)
            with patch.object(enqueuer, "backpressure_state", return_value="ok") as backpressure_state, patch.object(
                enqueuer.commands,
                "enqueue",
                return_value="command-id",
            ) as enqueue:
                self.assertEqual(enqueuer.enqueue_command({"kind": "publish_value", "path": "/Mode"}), "command-id")
            backpressure_state.assert_called_once_with(max_age_seconds=2.0)
            enqueue.assert_called_once_with({"kind": "publish_value", "path": "/Mode"})

            blocked = GatewayClient(paths)
            with patch.object(blocked, "backpressure_state", return_value="protective"), patch.object(
                blocked.commands,
                "enqueue",
                return_value="blocked",
            ) as enqueue:
                self.assertEqual(blocked.enqueue_command({"kind": "publish_value", "path": "/Auto/Reason"}), "")
            enqueue.assert_not_called()

            with patch.object(client, "enqueue_command", return_value="queued") as enqueue_command:
                client.publish_path("/Default", 7)
                client.publish_path("/Mode", 1, priority="user", source="gui")
                client.publish_paths({"/DefaultPaths": 8})
                client.publish_paths({"/Ac/Power": 1200, "": "ignored", "/Obj": object()}, source="core")
                client.publish_paths({"": "ignored"})
                client.publish_fields(
                    {"ac_power_w": 1300, "session_time_s": 31, "": "ignored", "debug_object": object()},
                    priority="user",
                    source="metrics",
                )
                client.register_path("/Readonly", 0)
                client.register_path("/Enable", True, writeable=True, source="startup")
                client.request_read(BATTERY_SOC_READ_KEY)
                client.request_read(GRID_POWER_READ_KEY, reason="stale")
                client.request_read(PV_POWER_READ_KEY, priority="read", source="helper", reason="missing")
                client.request_read_key(" battery_soc ", priority="safety", source="storage", reason="startup")
                client.request_raw_value(123, 456, priority="diagnostic", source="probe", reason="manual")

            queued = [call.args[0] for call in enqueue_command.call_args_list]
            self.assertEqual([command["kind"] for command in queued], [
                "publish_value",
                "publish_value",
                "publish_desired",
                "publish_desired",
                "publish_fields",
                "register_path",
                "register_path",
                "refresh_value",
                "refresh_value",
                "refresh_value",
                "refresh_value",
                "refresh_value",
            ])
            self.assertEqual(
                queued[0],
                {
                    "kind": "publish_value",
                    "source": "core",
                    "path": "/Default",
                    "value": 7,
                    "priority": "publish",
                    "coalesce_key": "publish:/Default",
                },
            )
            self.assertEqual(
                queued[1],
                {
                    "kind": "publish_value",
                    "source": "gui",
                    "path": "/Mode",
                    "value": 1,
                    "priority": "user",
                    "coalesce_key": "publish:/Mode",
                },
            )
            self.assertEqual(
                queued[2],
                {
                    "kind": "publish_desired",
                    "source": "core",
                    "paths": {"/DefaultPaths": 8},
                    "priority": "publish",
                    "coalesce_key": "publish:desired",
                },
            )
            self.assertEqual(queued[3]["paths"]["/Ac/Power"], 1200)
            self.assertIn("object object", queued[3]["paths"]["/Obj"])
            self.assertEqual(queued[3]["source"], "core")
            self.assertEqual(queued[3]["priority"], "publish")
            self.assertEqual(queued[3]["coalesce_key"], "publish:desired")
            self.assertEqual(queued[4]["fields"]["ac_power_w"], 1300)
            self.assertEqual(queued[4]["fields"]["session_time_s"], 31)
            self.assertIn("object object", queued[4]["fields"]["debug_object"])
            self.assertNotIn("", queued[4]["fields"])
            self.assertEqual(queued[4]["source"], "metrics")
            self.assertEqual(queued[4]["priority"], "user")
            self.assertEqual(queued[4]["coalesce_key"], "publish:fields")
            self.assertEqual(
                queued[5],
                {
                    "kind": "register_path",
                    "source": "core",
                    "path": "/Readonly",
                    "value": 0,
                    "writeable": False,
                    "priority": "publish",
                    "coalesce_key": "register:/Readonly",
                },
            )
            self.assertEqual(
                queued[6],
                {
                    "kind": "register_path",
                    "source": "startup",
                    "path": "/Enable",
                    "value": True,
                    "writeable": True,
                    "priority": "publish",
                    "coalesce_key": "register:/Enable",
                },
            )
            self.assertEqual(queued[7]["key"], BATTERY_SOC_READ_KEY)
            self.assertEqual(queued[7]["source"], "core")
            self.assertEqual(queued[7]["priority"], "read")
            self.assertEqual(queued[7]["reason"], "")
            self.assertEqual(queued[7]["coalesce_key"], f"refresh:{BATTERY_SOC_READ_KEY}")
            self.assertEqual(queued[8]["key"], GRID_POWER_READ_KEY)
            self.assertEqual(queued[8]["source"], "core")
            self.assertEqual(queued[8]["priority"], "read")
            self.assertEqual(queued[8]["reason"], "stale")
            self.assertEqual(queued[8]["coalesce_key"], f"refresh:{GRID_POWER_READ_KEY}")
            self.assertEqual(queued[9]["key"], PV_POWER_READ_KEY)
            self.assertEqual(queued[9]["source"], "helper")
            self.assertEqual(queued[9]["priority"], "read")
            self.assertEqual(queued[9]["reason"], "missing")
            self.assertEqual(queued[9]["coalesce_key"], f"refresh:{PV_POWER_READ_KEY}")
            self.assertEqual(queued[10]["key"], BATTERY_SOC_READ_KEY)
            self.assertEqual(queued[10]["source"], "storage")
            self.assertEqual(queued[10]["priority"], "safety")
            self.assertEqual(queued[10]["reason"], "startup")
            self.assertEqual(queued[10]["coalesce_key"], f"refresh:{BATTERY_SOC_READ_KEY}")
            self.assertEqual(queued[11]["service"], "123")
            self.assertEqual(queued[11]["path"], "456")
            self.assertEqual(queued[11]["source"], "probe")
            self.assertEqual(queued[11]["priority"], "diagnostic")
            self.assertEqual(queued[11]["reason"], "manual")
            self.assertEqual(queued[11]["coalesce_key"], "refresh:123:456")

            with patch.object(
                gateway_client_module.DbusCacheStore,
                "load_snapshot",
                return_value={"values": {"ok": {"value": 1}}},
            ) as load_snapshot:
                self.assertEqual(client.load_cache(), {"values": {"ok": {"value": 1}}})
            load_snapshot.assert_called_once_with(paths.cache_path, max_age_seconds=10.0)

            cache_snapshot = {
                "values": {
                    GRID_POWER_READ_KEY: {"status": "fresh", "age_s": 2.0, "value": -12.5},
                    PV_POWER_READ_KEY: {"status": "stale", "age_s": 11.0, "value": 200.0},
                }
            }
            with patch.object(client, "load_cache", return_value=cache_snapshot) as load_cache:
                self.assertEqual(client.read_key_value(" grid_power_w ", max_age_seconds=5.0), -12.5)
                self.assertIsNone(client.read_key_value(PV_POWER_READ_KEY, max_age_seconds=5.0))
            load_cache.assert_any_call(max_age_seconds=5.0)
            with self.assertRaisesRegex(ValueError, "Unsupported gateway read key"):
                client.read_key_value("path:svc/Raw")

            with patch.object(
                gateway_client_module.DbusCacheStore,
                "load_snapshot",
                return_value={"dbus_health": {"state": "ok"}},
            ) as load_snapshot:
                self.assertEqual(client.load_health(), {"state": "ok"})
            load_snapshot.assert_called_once_with(paths.health_path, max_age_seconds=10.0)

            cache_client = GatewayClient(paths)
            with patch.object(cache_client, "load_health", return_value={"backpressure": {"state": "slow"}}) as load_health, patch.object(
                gateway_client_module,
                "_now",
                return_value=10.0,
            ):
                self.assertEqual(cache_client.backpressure_state(max_age_seconds=2.5), "slow")
            load_health.assert_called_once_with(max_age_seconds=2.5)
            self.assertEqual(cache_client._backpressure_cache, (10.0, "slow"))

            with patch.object(cache_client, "load_health") as load_health, patch.object(gateway_client_module, "_now", return_value=10.5):
                self.assertEqual(cache_client.backpressure_state(), "slow")
            load_health.assert_not_called()

            with patch.object(cache_client, "load_health", return_value={}) as load_health, patch.object(
                gateway_client_module,
                "_now",
                return_value=11.1,
            ):
                self.assertEqual(cache_client.backpressure_state(), "unknown")
            load_health.assert_called_once_with(max_age_seconds=10.0)
            self.assertEqual(cache_client._backpressure_cache, (11.1, "unknown"))
            self.assertFalse(gateway_client_module._backpressure_cache_fresh(10.0, "unknown", 10.5))
            self.assertFalse(gateway_client_module._backpressure_cache_fresh(10.0, "ok", 11.0))
            self.assertTrue(gateway_client_module._backpressure_cache_fresh(10.0, "ok", 10.999))
            self.assertEqual(gateway_client_module._backpressure_state_from_health({}), "unknown")
            self.assertEqual(gateway_client_module._backpressure_state_from_health({"backpressure": []}), "unknown")
            self.assertEqual(gateway_client_module._backpressure_state_from_health({"backpressure": {"state": ""}}), "unknown")
            self.assertEqual(gateway_client_module._backpressure_state_from_health({"backpressure": {"state": "protective"}}), "protective")

    def test_gateway_client_default_read_and_publish_payload_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            client = GatewayClient(paths)

            with patch.object(client, "enqueue_command", return_value="queued") as enqueue_command:
                client.publish_fields({"ac_power_w": 1.5})
                client.request_read(PV_POWER_READ_KEY, priority="safety", source="helper", reason="missing")
                client.request_raw_value("svc", "/Path")
                client.request_read_key(BATTERY_SOC_READ_KEY)

            queued = [call.args[0] for call in enqueue_command.call_args_list]
            self.assertEqual(
                queued,
                [
                    {
                        "kind": "publish_fields",
                        "source": "core",
                        "fields": {"ac_power_w": 1.5},
                        "priority": "publish",
                        "coalesce_key": "publish:fields",
                    },
                    {
                        "kind": "refresh_value",
                        "source": "helper",
                        "priority": "safety",
                        "reason": "missing",
                        "key": PV_POWER_READ_KEY,
                        "coalesce_key": f"refresh:{PV_POWER_READ_KEY}",
                    },
                    {
                        "kind": "refresh_value",
                        "source": "core",
                        "service": "svc",
                        "path": "/Path",
                        "priority": "read",
                        "reason": "",
                        "coalesce_key": "refresh:svc:/Path",
                    },
                    {
                        "kind": "refresh_value",
                        "source": "core",
                        "priority": "read",
                        "reason": "",
                        "key": BATTERY_SOC_READ_KEY,
                        "coalesce_key": f"refresh:{BATTERY_SOC_READ_KEY}",
                    },
                ],
            )

            cache_snapshot = {"values": {GRID_POWER_READ_KEY: {"status": "fresh", "age_s": 9.5, "value": 44.0}}}
            with patch.object(client, "load_cache", return_value=cache_snapshot) as load_cache:
                self.assertEqual(client.read_key_value(GRID_POWER_READ_KEY), 44.0)
            load_cache.assert_called_once_with(max_age_seconds=10.0)

    def test_command_queue_class_maps_gateway_workloads(self) -> None:
        self.assertLess(
            dbus_gateway.PRIORITY_VALUES["normal"],
            dbus_gateway.PRIORITY_VALUES["diagnostic"],
        )
        cases = [
            ({"kind": "register_path"}, "startup/register"),
            ({"kind": "register_service"}, "startup/register"),
            ({"kind": "publish_value", "path": "/Mode"}, "gui-critical-publish"),
            ({"kind": "publish_value", "path": "/UpdateIndex"}, "gui-critical-publish"),
            ({"kind": "publish_desired", "paths": {"/Session/Time": 1}}, "gui-critical-publish"),
            ({"type": "publish_desired", "paths": {"/Mode": 1}}, "gui-critical-publish"),
            ({"kind": "publish_desired", "paths": {"/Auto/Reason": 1}}, "local-publish"),
            ({"kind": "publish_desired", "paths": ["/Mode"]}, "local-publish"),
            ({"kind": "publish_value", "path": "/Auto/Reason"}, "local-publish"),
            ({"kind": "publish_value", "path": ""}, "local-publish"),
            ({"kind": "set_value"}, "remote-write"),
            ({"type": "set_value"}, "remote-write"),
            ({"kind": "refresh_value", "key": "grid_power_w"}, "read-fast"),
            ({"kind": "refresh_value", "key": "pv_power_w"}, "read-fast"),
            ({"kind": "refresh_value", "key": "battery_soc"}, "read-fast"),
            ({"kind": "refresh_value", "key": "optional"}, "read-slow"),
            ({"kind": "refresh_value"}, "read-slow"),
            ({"kind": "refresh_services"}, "discovery"),
            ({"kind": "introspect"}, "introspection"),
            ({"kind": "unknown"}, "diagnostic"),
            ({}, "diagnostic"),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(command_queue_class(command), expected)
        self.assertTrue(command_allowed_by_backpressure({"kind": "register_path"}, "slow"))
        self.assertTrue(command_allowed_by_backpressure({"kind": "unknown"}, "mystery"))
        self.assertTrue(command_allowed_by_backpressure({"kind": "unknown"}, " OK "))
        self.assertTrue(command_allowed_by_backpressure({"kind": "unknown"}, ""))

    def test_backpressure_command_filter_keeps_critical_work(self) -> None:
        cases = [
            ({"kind": "publish_value", "path": "/Auto/Reason"}, "ok", True),
            ({"kind": "unknown", "priority": "diagnostic"}, "unknown", True),
            ({"kind": "register_path", "priority": "diagnostic"}, "protective", True),
            ({"kind": "publish_value", "path": "/Auto/Reason", "priority": "diagnostic"}, "congested", False),
            ({"kind": "publish_value", "path": "/Auto/Reason", "priority": "optional"}, "congested", False),
            ({"kind": "unknown", "priority": "normal"}, "congested", False),
            ({"kind": "refresh_value", "key": "grid_power_w", "priority": "read"}, "congested", True),
            ({"kind": "publish_value", "path": "/Mode"}, "slow", True),
            ({"kind": "publish_value", "path": "/Auto/Reason"}, "slow", False),
            ({"kind": "set_value", "priority": "user"}, "slow", True),
            ({"kind": "refresh_services", "priority": "safety"}, "slow", True),
            ({"kind": "refresh_services", "priority": "read"}, "slow", False),
            ({"kind": "publish_value", "path": "/StartStop", "priority": " USER "}, "protective", True),
            ({"kind": "set_value", "priority": "user"}, "protective", False),
            ({"kind": "refresh_services", "priority": "safety"}, "protective", True),
            ({"kind": "publish_value", "path": "/StartStop", "priority": "publish"}, "protective", False),
            ({"kind": "publish_value", "path": "/Mode", "queue_class": "diagnostic"}, "congested", False),
            ({"kind": "publish_value", "path": "/Mode", "priority": "normal", "queue_class": "diagnostic"}, "congested", False),
            (
                {"kind": "publish_value", "path": "/Auto/Reason", "priority": "user", "queue_class": "gui-critical-publish"},
                "protective",
                True,
            ),
            ({"kind": "publish_value", "path": "/Mode", "queue_class": ""}, "protective", False),
            ({"kind": "publish_value", "path": "/Mode", "priority": ""}, "congested", False),
            ({"kind": "publish_value", "path": "/Mode", "priority": " USER "}, " protective ", True),
            ({"kind": "refresh_value", "key": "grid_power_w", "priority": "read", "queue_class": ""}, " slow ", False),
        ]
        for command, state, expected in cases:
            with self.subTest(command=command, state=state):
                self.assertEqual(command_allowed_by_backpressure(command, state), expected)

    def test_gateway_proxy_gateway_value_and_latency_window(self) -> None:
        proxy_paths = gateway_paths("/tmp/proxy-gateway")
        with patch.object(gateway_client_module, "GatewayClient") as client_factory:
            constructed = GatewayDbusServiceProxy(123, paths=proxy_paths)
        client_factory.assert_called_once_with(proxy_paths)
        self.assertEqual(constructed.name, "123")

        fake_client = MagicMock()
        proxy = GatewayDbusServiceProxy("svc", client=fake_client)
        callback = MagicMock(return_value=True)
        proxy.add_path("/Mode", 1, gettextcallback=object(), writeable=True, onchangecallback=callback)
        proxy.add_path("/Readonly", 0)
        proxy.add_path("/StartStop", 0)
        proxy.register()
        self.assertEqual(proxy["/Mode"], 1)
        self.assertEqual(proxy._writeable, {"/Mode", "/StartStop"})
        self.assertEqual(set(proxy._callbacks), {"/Mode"})
        proxy["/Mode"] = 2
        self.assertEqual(proxy["/Mode"], 2)
        proxy.publish_paths({"/Mode": 5, "/Ac/Power": 1200.0, "": "ignored"})
        proxy.publish_fields({"ac_power_w": 1300.0, "session_energy_kwh": 0.2, "": "ignored"})
        self.assertEqual(proxy["/Mode"], 5)
        self.assertEqual(proxy["/Ac/Power"], 1300.0)
        self.assertEqual(proxy["/Session/Energy"], 0.2)
        self.assertTrue(proxy.apply_gateway_write("/Mode", 3))
        callback.assert_called_once_with("/Mode", 3)
        self.assertTrue(proxy.apply_gateway_write("/Other", 4))
        self.assertEqual(proxy["/Other"], 4)
        fake_client.register_path.assert_any_call("/Mode", 1, writeable=True)
        fake_client.register_path.assert_any_call("/Readonly", 0, writeable=False)
        fake_client.register_path.assert_any_call("/StartStop", 0, writeable=True)
        fake_client.publish_path.assert_called_once_with("/Mode", 2)
        fake_client.publish_paths.assert_called_once_with({"/Mode": 5, "/Ac/Power": 1200.0})
        fake_client.publish_fields.assert_called_once_with({"ac_power_w": 1300.0, "session_energy_kwh": 0.2})
        fake_client.enqueue_command.assert_called_once_with(
            {
                "kind": "register_service",
                "service": "svc",
                "source": "core",
                "priority": "publish",
                "coalesce_key": "register-service",
            }
        )

        snapshot = {
            "values": {
                "fresh": {"status": "fresh", "age_s": 1.0, "value": 1},
                "stale": {"status": "stale", "age_s": 2.0, "value": 2},
                "error": {"status": "error", "age_s": 0.0, "value": 3},
                "missing-status": {"age_s": 0.0, "value": 4},
                "blank-status": {"status": "", "age_s": 0.0, "value": 5},
                "bad-age": {"status": "fresh", "age_s": object(), "value": 6},
            }
        }
        self.assertEqual(gateway_value(snapshot, "fresh", max_age_seconds=5.0), 1)
        self.assertEqual(gateway_value(snapshot, "stale", max_age_seconds=5.0), 2)
        self.assertIsNone(gateway_value(snapshot, "missing", max_age_seconds=5.0))
        self.assertIsNone(gateway_value(snapshot, "error", max_age_seconds=5.0))
        self.assertIsNone(gateway_value(snapshot, "stale", max_age_seconds=1.0))
        self.assertIsNone(gateway_value(snapshot, "missing-status", max_age_seconds=5.0))
        self.assertIsNone(gateway_value(snapshot, "blank-status", max_age_seconds=5.0))
        self.assertEqual(gateway_value(snapshot, "bad-age", max_age_seconds=0.0), 6)
        self.assertEqual(dbus_path_key("svc", "/P"), "path:svc/P")

        self.assertEqual(LatencyWindow().window_seconds, 60.0)
        window = LatencyWindow(window_seconds=10.0)
        self.assertEqual(LatencyWindow(window_seconds=-5.0).window_seconds, 1.0)
        window.record_latency(-1.0, now=0.0)
        window.record_latency(20.0, now=5.0)
        window.record_timeout(now=5.0)
        self.assertEqual(window.summary(now=5.0)["max_latency_ms"], 20.0)
        self.assertEqual(window.summary(now=5.0)["avg_latency_ms"], 10.0)
        window.record_latency(30.0, now=20.0)
        summary = window.summary(now=20.0)
        self.assertEqual(summary["timeouts_60s"], 0)
        self.assertEqual(summary["avg_latency_ms"], 30.0)

        boundary = LatencyWindow(window_seconds=10.0)
        boundary.record_latency(10.0, now=0.0)
        boundary.record_timeout(now=0.0)
        self.assertEqual(boundary.summary(now=10.0), {
            "timeouts_60s": 1,
            "avg_latency_ms": 10.0,
            "max_latency_ms": 10.0,
        })
        self.assertEqual(boundary.summary(now=10.001), {
            "timeouts_60s": 0,
            "avg_latency_ms": 0.0,
            "max_latency_ms": 0.0,
        })
        implicit_now = LatencyWindow(window_seconds=2.0)
        with patch.object(gateway_latency_module, "_now", return_value=50.0):
            implicit_now.record_latency(8.0)
        with patch.object(gateway_latency_module, "_now", return_value=51.0):
            self.assertEqual(implicit_now.summary()["avg_latency_ms"], 8.0)


if __name__ == "__main__":
    unittest.main()
