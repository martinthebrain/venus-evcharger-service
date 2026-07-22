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
    DbusCacheStore,
    DbusGatewayCommandInbox,
    EVCS_FIELD_TO_PATH,
    EVCS_PATH_TO_FIELD,
    GatewayClient,
    GatewayPublicationClient,
    LatencyWindow,
    command_allowed_by_backpressure,
    command_queue_class,
    dbus_path_key,
    evcs_fields_to_paths,
    evcs_path_to_field,
    gateway_paths,
    gateway_value,
    read_json_file,
    write_json_file,
)
from venus_evcharger.dbus_gateway_commands import DbusGatewayCommandQueuePolicy
from venus_evcharger.ipc.command_mailbox import COMMAND_PRIORITY_RANKS, command_priority_rank
from venus_evcharger.ipc.energy import (
    ENERGY_REFRESH_COMMAND_KIND,
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergySourceDescriptor,
    EnergyTopologySnapshot,
    MeasuredValue,
)
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
    REGISTER_COMPANION_KIND,
    REGISTER_EVCS_KIND,
    parse_publish_companion_fields,
    parse_publish_evcs_fields,
    parse_register_companion,
    parse_register_evcs,
    publish_companion_fields_command,
    publish_evcs_fields_command,
    register_companion_command,
    register_evcs_command,
)
from venus_evcharger.ports.gateway_publication import CompanionServiceIdentity, EvcsServiceIdentity


def _evcs_identity() -> EvcsServiceIdentity:
    return EvcsServiceIdentity(
        product_name="EVCS",
        custom_name="Garage",
        firmware_version="1.2.3",
        hardware_version="relay",
        serial="evcs-60",
        connection_name="Local controller",
        process_name="venus_evcharger_service.py",
        process_version="Python",
    )


def _companion_identity(service_id: str = "battery-main") -> CompanionServiceIdentity:
    return CompanionServiceIdentity(
        service_id=service_id,
        kind="battery",
        product_name="Energy companion",
        custom_name="Battery",
        firmware_version="1.2.3",
        hardware_version="virtual",
        serial="battery-100",
        connection_name="Gateway",
        process_name="venus_evcharger_service.py",
        process_version="Python",
    )


def _energy_snapshots() -> tuple[EnergyInputsSnapshot, EnergyTopologySnapshot]:
    missing = MeasuredValue(None, 0.0, "unknown", 0.0, (), "not-observed")
    grid = MeasuredValue(-25.0, 100.0, "fresh", 1.0, ("grid-primary",))
    return (
        EnergyInputsSnapshot(3, 101.0, 2, grid, missing, missing),
        EnergyTopologySnapshot(
            2,
            101.0,
            (EnergySourceDescriptor("grid-primary", "grid", "online", ("power",)),),
        ),
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
            "auto_gateway_diagnostics_age": "/Auto/GatewayDiagnosticsAge",
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
            dbus_gateway_surface._snake_case("GatewayDiagnosticsAge"),
            "gateway_diagnostics_age",
        )
        self.assertEqual(
            dbus_gateway_surface._field_name_from_venus_path("/Auto/GatewayDiagnosticsAge"),
            "auto_gateway_diagnostics_age",
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

        self.assertEqual(command_priority_rank(" safety "), 0)
        self.assertEqual(command_priority_rank("USER"), 1)
        self.assertEqual(command_priority_rank("unknown"), COMMAND_PRIORITY_RANKS["diagnostic"])
        self.assertEqual(command_priority_rank(None), COMMAND_PRIORITY_RANKS["diagnostic"])

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

    def test_energy_snapshot_and_refresh_contracts_hide_adapter_targets(self) -> None:
        fresh_grid = MeasuredValue(-20.0, 100.0, "fresh", 1.0, ("grid-primary",))
        unavailable = MeasuredValue(None, 0.0, "unavailable", 0.0, (), "source-unavailable")
        inputs = EnergyInputsSnapshot(
            sequence=4,
            captured_at=101.0,
            topology_generation=2,
            grid_power_w=fresh_grid,
            pv_power_w=unavailable,
            battery_soc=unavailable,
        )
        self.assertEqual(EnergyInputsSnapshot.from_payload(inputs.to_payload()), inputs)

        topology = EnergyTopologySnapshot(
            generation=2,
            captured_at=101.0,
            sources=(EnergySourceDescriptor("grid-primary", "grid", "online", ("power",)),),
        )
        self.assertEqual(EnergyTopologySnapshot.from_payload(topology.to_payload()), topology)

        request = EnergyRefreshRequest(
            request_id="refresh-grid",
            scope="grid",
            max_age_seconds=5.0,
            urgency="priority",
            reason="stale input",
        )
        command = request.to_command(source="auto-input-helper")
        self.assertEqual(command["kind"], ENERGY_REFRESH_COMMAND_KIND)
        self.assertEqual(EnergyRefreshRequest.from_command(command), request)
        self.assertTrue({"service", "path", "key"}.isdisjoint(command))

        for adapter_detail in ({"service": "svc"}, {"path": "/Raw"}, {"key": "grid_power_w"}):
            with self.subTest(adapter_detail=adapter_detail):
                with self.assertRaisesRegex(ValueError, "must not expose adapter targets"):
                    EnergyRefreshRequest.from_command({**command, **adapter_detail})

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
            inbox = DbusGatewayCommandInbox(str(Path(temp_dir) / "commands"))

            first_command = publish_evcs_fields_command({"mode": 0}, priority="live")
            first_command["created_at"] = 1.0
            first = inbox.enqueue(first_command)
            second_command = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
            second_command["created_at"] = 2.0
            second = inbox.enqueue(second_command)

            self.assertEqual(first, second)
            pending_publish = read_json_file(first, {})
            self.assertEqual(pending_publish["kind"], PUBLISH_EVCS_FIELDS_KIND)
            self.assertEqual(pending_publish["fields"], {"mode": 0, "ac_power_w": 1200.0})
            self.assertEqual(pending_publish["queue_class"], "local-publish")
            self.assertEqual(pending_publish["lifecycle_state"], "coalesced")
            self.assertEqual(pending_publish["created_at"], 1.0)
            self.assertGreaterEqual(pending_publish["updated_at"], 2.0)

            critical = publish_evcs_fields_command({"connected": 1}, priority="critical")
            critical["created_at"] = 3.0
            critical_path = inbox.enqueue(critical)
            self.assertNotEqual(critical_path, first)
            self.assertEqual(read_json_file(critical_path, {})["queue_class"], "gui-critical-publish")

            refresh = EnergyRefreshRequest("refresh-grid-1", "grid", 5.0, "priority").to_command(
                source="helper"
            )
            refresh["created_at"] = 4.0
            refresh_path = inbox.enqueue(refresh)
            newer_refresh = EnergyRefreshRequest(
                "refresh-grid-2",
                "grid",
                1.0,
                "priority",
                reason="stale",
            ).to_command(source="helper")
            newer_refresh["created_at"] = 5.0
            self.assertEqual(inbox.enqueue(newer_refresh), refresh_path)
            pending_refresh = read_json_file(refresh_path, {})
            self.assertEqual(pending_refresh["kind"], ENERGY_REFRESH_COMMAND_KIND)
            self.assertEqual(pending_refresh["request_id"], "refresh-grid-2")
            self.assertEqual(pending_refresh["queue_class"], "read-fast")
            self.assertTrue({"service", "path", "key"}.isdisjoint(pending_refresh))

            companion_a = publish_companion_fields_command(
                "battery-a",
                {"soc_percent": 80.0},
                priority="live",
            )
            companion_b = publish_companion_fields_command(
                "battery-b",
                {"soc_percent": 70.0},
                priority="live",
            )
            companion_a_path = inbox.enqueue(companion_a)
            companion_b_path = inbox.enqueue(companion_b)
            self.assertNotEqual(companion_a_path, companion_b_path)

            self.assertEqual(inbox.remove_coalesced(""), 0)
            self.assertEqual(inbox.remove_coalesced(str(refresh["coalesce_key"])), 1)
            self.assertFalse(Path(refresh_path).exists())

            ordered = inbox.coalesce(
                [
                    ("diagnostic", {"id": "diagnostic", "priority": "diagnostic"}),
                    (
                        "older-live",
                        {
                            "id": "older-live",
                            "kind": PUBLISH_EVCS_FIELDS_KIND,
                            "priority": "publish",
                            "created_at": 10.0,
                            "coalesce_key": "same",
                        },
                    ),
                    (
                        "newer-live",
                        {
                            "id": "newer-live",
                            "kind": PUBLISH_EVCS_FIELDS_KIND,
                            "priority": "publish",
                            "created_at": 11.0,
                            "coalesce_key": "same",
                        },
                    ),
                    (
                        "critical",
                        {
                            "id": "critical",
                            "kind": PUBLISH_EVCS_FIELDS_KIND,
                            "priority": "safety",
                            "created_at": 12.0,
                        },
                    ),
                ]
            )
            self.assertEqual([item[0] for item in ordered], ["critical", "newer-live", "diagnostic"])

            Path(inbox.command_dir, "bad.json").write_text("{", encoding="utf-8")
            Path(inbox.command_dir, "list.json").write_text("[]", encoding="utf-8")
            self.assertTrue(inbox.load_pending())
            inbox.remove(str(Path(inbox.command_dir) / "missing.json"))
            with patch.object(dbus_gateway.Path, "glob", side_effect=OSError("boom")):
                self.assertEqual(inbox.load_pending(), [])

            malformed_target = inbox.enqueue(
                publish_companion_fields_command(
                    "battery-malformed",
                    {"soc_percent": 1.0},
                    priority="live",
                )
            )
            Path(malformed_target).write_text("[]", encoding="utf-8")
            replacement_command = publish_companion_fields_command(
                "battery-malformed",
                {"soc_percent": 2.0},
                priority="live",
            )
            self.assertEqual(inbox.enqueue(replacement_command), malformed_target)
            self.assertEqual(read_json_file(malformed_target, {})["fields"], {"soc_percent": 2.0})

    def test_gateway_command_policy_covers_publish_merge_and_ordering(self) -> None:
        policy = DbusGatewayCommandQueuePolicy()
        evcs_register = register_evcs_command(_evcs_identity(), {"mode": 0})
        evcs_live = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        evcs_critical = publish_evcs_fields_command({"connected": 1}, priority="critical")
        companion_live = publish_companion_fields_command(
            "battery-main",
            {"soc_percent": 80.0},
            priority="live",
        )
        refresh = EnergyRefreshRequest("refresh-grid", "grid", 5.0, "priority").to_command(
            source="helper"
        )
        topology_refresh = EnergyRefreshRequest("refresh-topology", "topology", 60.0).to_command(
            source="helper"
        )

        self.assertEqual(policy.normalize(evcs_register), evcs_register)
        self.assertIsNot(policy.normalize(evcs_register), evcs_register)
        self.assertEqual(policy.queue_class(evcs_register), "startup/register")
        self.assertEqual(policy.queue_class(evcs_live), "local-publish")
        self.assertEqual(policy.queue_class(evcs_critical), "gui-critical-publish")
        self.assertEqual(policy.queue_class(refresh), "read-fast")
        self.assertEqual(policy.queue_class(topology_refresh), "discovery")

        self.assertTrue(
            dbus_gateway_commands._same_publish_fields_payload(
                publish_evcs_fields_command({"mode": 0}, priority="live"),
                publish_evcs_fields_command({"ac_power_w": 1.0}, priority="live"),
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_fields_payload(
                publish_evcs_fields_command({"mode": 0}, priority="critical"),
                publish_evcs_fields_command({"ac_power_w": 1.0}, priority="live"),
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_fields_payload(
                evcs_live,
                companion_live,
            )
        )
        self.assertTrue(
            dbus_gateway_commands._same_publish_fields_payload(
                publish_companion_fields_command(
                    "battery-main",
                    {"soc_percent": 79.0},
                    priority="live",
                ),
                companion_live,
            )
        )
        self.assertFalse(
            dbus_gateway_commands._same_publish_fields_payload(
                publish_companion_fields_command(
                    "battery-other",
                    {"soc_percent": 79.0},
                    priority="live",
                ),
                companion_live,
            )
        )
        self.assertFalse(dbus_gateway_commands._same_publish_fields_payload([], evcs_live))

        merged_evcs = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        policy.merge_coalesced(
            publish_evcs_fields_command({"mode": 0}, priority="live"),
            merged_evcs,
        )
        self.assertEqual(merged_evcs["fields"], {"mode": 0, "ac_power_w": 1200.0})

        merged_companion = publish_companion_fields_command(
            "battery-main",
            {"soc_percent": 80.0},
            priority="live",
        )
        policy.merge_coalesced(
            publish_companion_fields_command(
                "battery-main",
                {"connected": 1},
                priority="live",
            ),
            merged_companion,
        )
        self.assertEqual(merged_companion["fields"], {"connected": 1, "soc_percent": 80.0})

        unchanged = publish_evcs_fields_command({"mode": 2}, priority="live")
        policy.merge_coalesced(None, unchanged)
        self.assertEqual(unchanged["fields"], {"mode": 2})
        policy.merge_coalesced(evcs_register, unchanged)
        self.assertEqual(unchanged["fields"], {"mode": 2})

        commands = [
            {**refresh, "id": "refresh", "created_at": 4.0},
            {**evcs_live, "id": "live", "created_at": 3.0},
            {**evcs_register, "id": "register", "created_at": 2.0},
            {**evcs_critical, "id": "critical", "created_at": 1.0},
        ]
        self.assertEqual(
            [command["id"] for command in sorted(commands, key=policy.order_key)],
            ["critical", "register", "live", "refresh"],
        )
        self.assertEqual(policy.order_key({}), (6, 2, 0.0, 0, ""))
        self.assertEqual(dbus_gateway_commands._command_kind_rank(evcs_register), 0)
        self.assertEqual(
            dbus_gateway_commands._command_kind_rank(
                register_companion_command(_companion_identity(), {"connected": 1})
            ),
            0,
        )
        self.assertEqual(dbus_gateway_commands._command_kind_rank(evcs_live), 1)
        self.assertEqual(dbus_gateway_commands._command_kind_rank(companion_live), 1)
        self.assertEqual(dbus_gateway_commands._command_kind_rank(refresh), 2)
        self.assertEqual(dbus_gateway_commands._command_kind({"type": ENERGY_REFRESH_COMMAND_KIND}), ENERGY_REFRESH_COMMAND_KIND)
        self.assertEqual(dbus_gateway_commands._command_kind({}), "")

    def test_gateway_client_transport_and_semantic_command_helpers(self) -> None:
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
                with patch.object(gateway_client_module.socket, "socket", return_value=FakeSocket(response)):
                    self.assertEqual(client.send({"kind": "health"})["ok"], expected_ok)

            publication = GatewayPublicationClient(client)
            register_receipt = publication.register_evcs(
                _evcs_identity(),
                {"mode": 0, "connected": 1},
            )
            publish_receipt = publication.publish_evcs_fields(
                {"ac_power_w": 1200.0, "session_time_s": 30},
                priority="live",
            )
            refresh_path = client.request_energy_refresh(
                EnergyRefreshRequest(
                    "refresh-energy",
                    "all",
                    5.0,
                    "priority",
                    reason="inputs stale",
                ),
                source="auto-input-helper",
            )
            self.assertTrue(register_receipt.accepted)
            self.assertTrue(register_receipt.command_id)
            self.assertTrue(publish_receipt.accepted)
            self.assertTrue(refresh_path)

            pending = DbusGatewayCommandInbox(paths.command_dir).load_pending()
            self.assertEqual(len(pending), 3)
            by_kind = {str(command["kind"]): command for _path, command in pending}

            registration = parse_register_evcs(by_kind[REGISTER_EVCS_KIND])
            self.assertIsNotNone(registration)
            assert registration is not None
            self.assertEqual(registration.identity, _evcs_identity())
            self.assertEqual(registration.initial_fields, {"mode": 0, "connected": 1})

            publication_payload = parse_publish_evcs_fields(by_kind[PUBLISH_EVCS_FIELDS_KIND])
            self.assertIsNotNone(publication_payload)
            assert publication_payload is not None
            self.assertEqual(
                publication_payload.fields,
                {"ac_power_w": 1200.0, "session_time_s": 30},
            )

            refresh_payload = by_kind[ENERGY_REFRESH_COMMAND_KIND]
            self.assertEqual(
                EnergyRefreshRequest.from_command(refresh_payload),
                EnergyRefreshRequest(
                    "refresh-energy",
                    "all",
                    5.0,
                    "priority",
                    reason="inputs stale",
                ),
            )
            self.assertTrue({"service", "path", "key"}.isdisjoint(refresh_payload))

            inputs, topology = _energy_snapshots()
            store = DbusCacheStore(paths)
            store.set_semantic_energy_snapshots(inputs, topology)
            store.health["backpressure"] = {"state": "slow"}
            store.write_snapshot_files()
            self.assertEqual(client.load_energy_inputs(), inputs)
            self.assertEqual(client.load_energy_topology(), topology)
            self.assertEqual(client.load_health()["backpressure"]["state"], "slow")
            self.assertEqual(client.backpressure_state(), "slow")

            blocked = publication.publish_evcs_fields({"diagnostic_text": "optional"}, priority="diagnostic")
            self.assertFalse(blocked.accepted)
            critical = publication.publish_evcs_fields({"connected": 0}, priority="critical")
            self.assertTrue(critical.accepted)
            self.assertEqual(len(DbusGatewayCommandInbox(paths.command_dir).load_pending()), 4)

    def test_gateway_client_contracts_for_semantic_commands_and_backpressure_cache(self) -> None:
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
                    self.recv_size = 0

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
                response = client.send({"kind": "health", "value": object()})
            socket_factory.assert_called_once_with(
                gateway_client_module.socket.AF_UNIX,
                gateway_client_module.socket.SOCK_STREAM,
            )
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
                self.assertEqual(
                    client.send({"kind": "health"}),
                    {"ok": False, "error": "invalid-response"},
                )

            class FailingSocket(InspectableSocket):
                def connect(self, path: str) -> None:
                    self.connected_path = path
                    raise RuntimeError("offline")

            with patch.object(
                gateway_client_module.socket,
                "socket",
                return_value=FailingSocket(b""),
            ):
                self.assertEqual(
                    client.send({"kind": "health"}),
                    {"ok": False, "error": "offline"},
                )

            semantic_publish = publish_evcs_fields_command({"mode": 2}, priority="critical")
            enqueuer = GatewayClient(paths)
            with patch.object(
                enqueuer,
                "backpressure_state",
                return_value="ok",
            ) as backpressure_state, patch.object(
                enqueuer.commands,
                "enqueue",
                return_value="command-id",
            ) as enqueue:
                self.assertEqual(enqueuer.enqueue_command(semantic_publish), "command-id")
            backpressure_state.assert_called_once_with(max_age_seconds=2.0)
            enqueue.assert_called_once_with(semantic_publish)

            blocked_command = publish_evcs_fields_command(
                {"diagnostic_text": "details"},
                priority="diagnostic",
            )
            blocked = GatewayClient(paths)
            with patch.object(blocked, "backpressure_state", return_value="protective"), patch.object(
                blocked.commands,
                "enqueue",
                return_value="blocked",
            ) as enqueue:
                self.assertEqual(blocked.enqueue_command(blocked_command), "")
            enqueue.assert_not_called()

            inputs, topology = _energy_snapshots()
            publication = GatewayPublicationClient(client)
            with patch.object(client, "enqueue_command", return_value="/tmp/semantic-command.json") as enqueue_command:
                evcs_register_receipt = publication.register_evcs(_evcs_identity(), {"mode": 0})
                evcs_publish_receipt = publication.publish_evcs_fields(
                    {"ac_power_w": 1300.0},
                    priority="live",
                )
                companion_register_receipt = publication.register_companion(
                    _companion_identity(),
                    {"connected": 1, "soc_percent": 80.0},
                )
                companion_publish_receipt = publication.publish_companion_fields(
                    "battery-main",
                    {"soc_percent": 79.0},
                    priority="diagnostic",
                )
                refresh_result = client.request_energy_refresh(
                    EnergyRefreshRequest(
                        "refresh-source",
                        "energy_source",
                        0.0,
                        "normal",
                        source_id="battery-main",
                    ),
                    source="auto-input-helper",
                )

            self.assertTrue(evcs_register_receipt.accepted)
            self.assertEqual(evcs_register_receipt.command_id, "semantic-command")
            self.assertTrue(evcs_publish_receipt.accepted)
            self.assertTrue(companion_register_receipt.accepted)
            self.assertTrue(companion_publish_receipt.accepted)
            self.assertEqual(refresh_result, "/tmp/semantic-command.json")

            queued = [call.args[0] for call in enqueue_command.call_args_list]
            self.assertEqual(
                [command["kind"] for command in queued],
                [
                    REGISTER_EVCS_KIND,
                    PUBLISH_EVCS_FIELDS_KIND,
                    REGISTER_COMPANION_KIND,
                    PUBLISH_COMPANION_FIELDS_KIND,
                    ENERGY_REFRESH_COMMAND_KIND,
                ],
            )
            parsed_evcs_registration = parse_register_evcs(queued[0])
            parsed_evcs_publication = parse_publish_evcs_fields(queued[1])
            parsed_companion_registration = parse_register_companion(queued[2])
            parsed_companion_publication = parse_publish_companion_fields(queued[3])
            self.assertIsNotNone(parsed_evcs_registration)
            self.assertIsNotNone(parsed_evcs_publication)
            self.assertIsNotNone(parsed_companion_registration)
            self.assertIsNotNone(parsed_companion_publication)
            assert parsed_evcs_registration is not None
            assert parsed_evcs_publication is not None
            assert parsed_companion_registration is not None
            assert parsed_companion_publication is not None
            self.assertEqual(parsed_evcs_registration.identity, _evcs_identity())
            self.assertEqual(parsed_evcs_publication.fields, {"ac_power_w": 1300.0})
            self.assertEqual(parsed_companion_registration.identity, _companion_identity())
            self.assertEqual(parsed_companion_publication.service_id, "battery-main")
            self.assertEqual(EnergyRefreshRequest.from_command(queued[4]).source_id, "battery-main")
            self.assertTrue(all({"service", "path", "key"}.isdisjoint(command) for command in queued))

            with patch.object(
                gateway_client_module.DbusCacheStore,
                "load_snapshot",
                return_value={"energy_inputs": inputs.to_payload()},
            ) as load_snapshot:
                self.assertEqual(client.load_cache(), {"energy_inputs": inputs.to_payload()})
            load_snapshot.assert_called_once_with(paths.cache_path, max_age_seconds=10.0)

            with patch.object(client, "load_cache", return_value={"energy_inputs": inputs.to_payload()}) as load_cache:
                self.assertEqual(client.load_energy_inputs(max_age_seconds=4.0), inputs)
            load_cache.assert_called_once_with(max_age_seconds=4.0)
            with patch.object(client, "load_cache", return_value={"energy_inputs": {"invalid": True}}):
                self.assertIsNone(client.load_energy_inputs())

            with patch.object(client, "load_cache", return_value={"energy_topology": topology.to_payload()}) as load_cache:
                self.assertEqual(client.load_energy_topology(max_age_seconds=12.0), topology)
            load_cache.assert_called_once_with(max_age_seconds=12.0)
            with patch.object(client, "load_cache", return_value={}):
                self.assertIsNone(client.load_energy_topology())

            with patch.object(
                gateway_client_module.DbusCacheStore,
                "load_snapshot",
                return_value={"dbus_health": {"state": "ok"}},
            ) as load_snapshot:
                self.assertEqual(client.load_health(), {"state": "ok"})
            load_snapshot.assert_called_once_with(paths.health_path, max_age_seconds=10.0)

            cache_client = GatewayClient(paths)
            with patch.object(
                cache_client,
                "load_health",
                return_value={"backpressure": {"state": "slow"}},
            ) as load_health, patch.object(gateway_client_module, "_now", return_value=10.0):
                self.assertEqual(cache_client.backpressure_state(max_age_seconds=2.5), "slow")
            load_health.assert_called_once_with(max_age_seconds=2.5)
            self.assertEqual(cache_client._backpressure_cache, (10.0, "slow"))

            with patch.object(cache_client, "load_health") as load_health, patch.object(
                gateway_client_module,
                "_now",
                return_value=10.5,
            ):
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
            self.assertEqual(
                gateway_client_module._backpressure_state_from_health({"backpressure": []}),
                "unknown",
            )
            self.assertEqual(
                gateway_client_module._backpressure_state_from_health({"backpressure": {"state": ""}}),
                "unknown",
            )
            self.assertEqual(
                gateway_client_module._backpressure_state_from_health(
                    {"backpressure": {"state": "protective"}}
                ),
                "protective",
            )

    def test_gateway_client_default_energy_and_publication_payload_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            client = GatewayClient(paths)
            publication = GatewayPublicationClient(client)
            refresh = EnergyRefreshRequest(
                request_id="refresh-pv",
                scope="pv",
                max_age_seconds=10.0,
                reason="missing",
            )

            with patch.object(client, "enqueue_command", return_value="/tmp/queued.json") as enqueue_command:
                publication.publish_evcs_fields({"ac_power_w": 1.5}, priority="live")
                client.request_energy_refresh(refresh, source="helper")

            queued = [call.args[0] for call in enqueue_command.call_args_list]
            self.assertEqual(
                queued,
                [
                    publish_evcs_fields_command({"ac_power_w": 1.5}, priority="live"),
                    refresh.to_command(source="helper"),
                ],
            )
            self.assertTrue(all({"service", "path", "key"}.isdisjoint(command) for command in queued))

            inputs, topology = _energy_snapshots()
            with patch.object(
                client,
                "load_cache",
                return_value={
                    "energy_inputs": inputs.to_payload(),
                    "energy_topology": topology.to_payload(),
                },
            ) as load_cache:
                self.assertEqual(client.load_energy_inputs(), inputs)
                self.assertEqual(client.load_energy_topology(), topology)
            self.assertEqual(
                [call.kwargs for call in load_cache.call_args_list],
                [{"max_age_seconds": 10.0}, {"max_age_seconds": 30.0}],
            )

    def test_command_queue_class_maps_semantic_gateway_workloads(self) -> None:
        self.assertLess(
            COMMAND_PRIORITY_RANKS["normal"],
            COMMAND_PRIORITY_RANKS["diagnostic"],
        )
        cases = [
            (register_evcs_command(_evcs_identity(), {"mode": 0}), "startup/register"),
            (
                register_companion_command(_companion_identity(), {"connected": 1}),
                "startup/register",
            ),
            (
                publish_evcs_fields_command({"connected": 1}, priority="critical"),
                "gui-critical-publish",
            ),
            (
                publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live"),
                "local-publish",
            ),
            (
                publish_companion_fields_command(
                    "battery-main",
                    {"soc_percent": 80.0},
                    priority="diagnostic",
                ),
                "local-publish",
            ),
            (
                EnergyRefreshRequest("refresh-grid", "grid", 5.0, "priority").to_command(
                    source="helper"
                ),
                "read-fast",
            ),
            (
                EnergyRefreshRequest("refresh-topology", "topology", 60.0).to_command(
                    source="helper"
                ),
                "discovery",
            ),
            ({"kind": "introspect"}, "introspection"),
            ({"kind": "gx_relay_refresh"}, "read-fast"),
            ({"kind": "gx_relay_set_enabled"}, "remote-write"),
            ({"kind": "ess_grid_setpoint"}, "remote-write"),
            ({"kind": "unknown"}, "diagnostic"),
            ({}, "diagnostic"),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(command_queue_class(command), expected)

        registration = register_evcs_command(_evcs_identity(), {"mode": 0})
        self.assertTrue(command_allowed_by_backpressure(registration, "slow"))
        self.assertTrue(command_allowed_by_backpressure({"kind": "unknown"}, "mystery"))
        self.assertTrue(command_allowed_by_backpressure({"kind": "unknown"}, " OK "))
        self.assertTrue(command_allowed_by_backpressure({"kind": "unknown"}, ""))

    def test_backpressure_filter_uses_semantic_priority_and_queue_class(self) -> None:
        registration = register_evcs_command(_evcs_identity(), {"mode": 0})
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")
        live = publish_evcs_fields_command({"ac_power_w": 1000.0}, priority="live")
        diagnostic = publish_evcs_fields_command(
            {"diagnostic_text": "details"},
            priority="diagnostic",
        )
        priority_refresh = EnergyRefreshRequest(
            "refresh-grid",
            "grid",
            5.0,
            "priority",
        ).to_command(source="helper")
        topology_refresh = EnergyRefreshRequest(
            "refresh-topology",
            "topology",
            60.0,
        ).to_command(source="helper")

        cases = [
            (registration, "protective", True),
            (critical, "congested", True),
            (critical, "slow", True),
            (critical, "protective", True),
            (live, "congested", True),
            (live, "slow", False),
            (live, "protective", False),
            (diagnostic, "congested", False),
            (priority_refresh, "congested", True),
            (priority_refresh, "slow", False),
            (priority_refresh, "protective", False),
            (topology_refresh, "congested", True),
            (topology_refresh, "slow", False),
            ({**critical, "queue_class": "diagnostic"}, "congested", False),
            ({"kind": "unknown", "priority": "normal"}, "congested", False),
            ({"kind": "unknown", "priority": "diagnostic"}, "unknown", True),
            (critical, " protective ", True),
        ]
        for command, state, expected in cases:
            with self.subTest(command=command, state=state):
                self.assertEqual(command_allowed_by_backpressure(command, state), expected)

    def test_publication_client_gateway_value_and_latency_window(self) -> None:
        fake_client = MagicMock()
        fake_client.enqueue_command.side_effect = [
            "/tmp/register-evcs.json",
            "/tmp/publish-evcs.json",
            "/tmp/register-companion.json",
            "/tmp/publish-companion.json",
            "",
        ]
        publication = GatewayPublicationClient(fake_client)

        registered_evcs = publication.register_evcs(_evcs_identity(), {"mode": 0})
        published_evcs = publication.publish_evcs_fields({"mode": 2}, priority="critical")
        registered_companion = publication.register_companion(
            _companion_identity(),
            {"connected": 1},
        )
        published_companion = publication.publish_companion_fields(
            "battery-main",
            {"soc_percent": 78.0},
            priority="live",
        )
        rejected = publication.publish_evcs_fields(
            {"diagnostic_text": "optional"},
            priority="diagnostic",
        )

        self.assertEqual(registered_evcs.command_id, "register-evcs")
        self.assertEqual(published_evcs.command_id, "publish-evcs")
        self.assertEqual(registered_companion.command_id, "register-companion")
        self.assertEqual(published_companion.command_id, "publish-companion")
        self.assertTrue(registered_evcs.accepted)
        self.assertTrue(published_evcs.accepted)
        self.assertTrue(registered_companion.accepted)
        self.assertTrue(published_companion.accepted)
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.command_id, "")

        commands = [call.args[0] for call in fake_client.enqueue_command.call_args_list]
        evcs_registration = parse_register_evcs(commands[0])
        evcs_publication = parse_publish_evcs_fields(commands[1])
        companion_registration = parse_register_companion(commands[2])
        companion_publication = parse_publish_companion_fields(commands[3])
        self.assertIsNotNone(evcs_registration)
        self.assertIsNotNone(evcs_publication)
        self.assertIsNotNone(companion_registration)
        self.assertIsNotNone(companion_publication)
        assert evcs_registration is not None
        assert evcs_publication is not None
        assert companion_registration is not None
        assert companion_publication is not None
        self.assertEqual(evcs_registration.identity, _evcs_identity())
        self.assertEqual(evcs_publication.fields, {"mode": 2})
        self.assertEqual(companion_registration.identity, _companion_identity())
        self.assertEqual(companion_publication.service_id, "battery-main")
        self.assertTrue(all({"service", "path", "key"}.isdisjoint(command) for command in commands))

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


if __name__ == "__main__":
    unittest.main()
