#!/usr/bin/env python3
"""Behavioral contracts for gateway-owned DBus introspection snapshots."""

from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as xml_et
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

import venus_evcharger.dbus_adapter.process.introspection_snapshot as snapshot
from venus_evcharger.dbus_adapter.process.introspection_snapshot import DBUS_INTROSPECTION_SCHEMA_VERSION


def adapter(values: dict[str, dict[str, object]] | None = None) -> snapshot.DbusAdapterIntrospectionSnapshot:
    instance = object.__new__(snapshot.DbusAdapterIntrospectionSnapshot)
    instance.cache = SimpleNamespace(values=values or {})
    instance.dbus_introspection_enabled = True
    instance.dbus_introspection_snapshot_path = "/run/introspection.json"
    instance._introspection_queue_depth = 3
    instance._last_introspection_full_scan_at = 90.0
    return instance


class DbusAdapterIntrospectionSnapshotContractTests(unittest.TestCase):
    def test_fresh_finding_has_exact_defaults_and_xml_capabilities(self) -> None:
        finding = snapshot._fresh_introspection_finding(
            {
                "status": "fresh",
                "value": "<node><interface name='iface'/><interface/><node name='Child'/><node name=''/></node>",
            },
            100.0,
        )
        self.assertEqual(
            finding,
            {
                "status": "fresh",
                "confidence": 0.8,
                "interfaces": ["iface"],
                "children": ["Child"],
                "source": "gateway",
                "reason": "gateway-introspection",
                "last_success_at": 100.0,
                "last_error": "",
                "retry_after": 100.0,
            },
        )
        custom = snapshot._fresh_introspection_finding(
            {"value": "<node/>", "confidence": 0.6, "source": "svc/path", "updated_at": 88.0},
            100.0,
        )
        self.assertEqual(custom["confidence"], 0.6)
        self.assertEqual(custom["source"], "svc/path")
        self.assertEqual(custom["last_success_at"], 88.0)

    def test_backoff_finding_maps_error_other_and_empty_status(self) -> None:
        expected_common = {
            "confidence": 0.55,
            "interfaces": [],
            "children": [],
            "source": "gateway",
            "reason": "gateway-introspection",
            "last_success_at": None,
            "last_error": "",
            "retry_after": 1000.0,
        }
        self.assertEqual(snapshot._backoff_introspection_finding({}, "error", 100.0), expected_common | {"status": "unresponsive-backoff"})
        self.assertEqual(snapshot._backoff_introspection_finding({}, "stale", 100.0), expected_common | {"status": "stale"})
        self.assertEqual(snapshot._backoff_introspection_finding({}, "", 100.0), expected_common | {"status": "unknown"})
        custom = snapshot._backoff_introspection_finding({"source": "svc/path", "last_error": "offline"}, "error", 100.0)
        self.assertEqual(custom["source"], "svc/path")
        self.assertEqual(custom["last_error"], "offline")

    def test_paths_payload_preserves_dict_and_replaces_invalid_value(self) -> None:
        paths = {"/A": {"status": "fresh"}}
        payload = {"paths": paths}
        self.assertIs(snapshot._paths_payload(payload), paths)
        for invalid in (None, "bad", [], 3):
            with self.subTest(invalid=invalid):
                payload = {"paths": invalid}
                normalized = snapshot._paths_payload(payload)
                self.assertEqual(normalized, {})
                self.assertIs(payload["paths"], normalized)

    def test_key_split_xml_parse_and_finding_dispatch_are_explicit(self) -> None:
        self.assertEqual(snapshot.DbusAdapterIntrospectionSnapshot.split_introspection_cache_key("introspection:svc:/Path"), ("svc", "/Path"))
        self.assertEqual(snapshot.DbusAdapterIntrospectionSnapshot.split_introspection_cache_key("introspection:svc"), ("svc", "/"))
        self.assertEqual(snapshot.DbusAdapterIntrospectionSnapshot.parse_introspection_xml("<bad"), ([], []))
        root = xml_et.fromstring("<node><interface name='A'/><interface/><interface name='B'/></node>")
        self.assertEqual(snapshot._xml_names(root, "interface"), ["A", "B"])
        fresh_entry = {"status": "fresh", "value": "<node/>", "updated_at": 90.0}
        self.assertEqual(
            snapshot.DbusAdapterIntrospectionSnapshot.introspection_finding(fresh_entry, 100.0),
            snapshot._fresh_introspection_finding(fresh_entry, 100.0),
        )
        error_entry = {"status": "error", "last_error": "offline"}
        self.assertEqual(
            snapshot.DbusAdapterIntrospectionSnapshot.introspection_finding(error_entry, 100.0),
            snapshot._backoff_introspection_finding(error_entry, "error", 100.0),
        )
        self.assertEqual(
            snapshot.DbusAdapterIntrospectionSnapshot.introspection_finding({}, 100.0),
            snapshot._backoff_introspection_finding({}, "", 100.0),
        )

    def test_cache_entries_filter_prefix_and_mapping_payload(self) -> None:
        instance = adapter(
            {
                "introspection:svc:/A": {"status": "fresh"},
                "introspection:svc:/Bad": "bad",
                "path:svc/A": {"status": "fresh"},
            }
        )
        self.assertEqual(instance.introspection_cache_entries(), [("introspection:svc:/A", {"status": "fresh"})])

    def test_service_aggregation_skips_empty_service_and_tracks_latest_update(self) -> None:
        instance = adapter()
        services: dict[str, dict[str, object]] = {}
        instance.add_introspection_service_entry(services, "", "/Ignored", {"status": "error"}, 100.0)
        self.assertEqual(services, {})
        instance.add_introspection_service_entry(
            services,
            "svc",
            "/A",
            {"status": "fresh", "updated_at": 90.0, "value": "<node/>"},
            100.0,
        )
        instance.add_introspection_service_entry(
            services,
            "svc",
            "/B",
            {"status": "error", "updated_at": 110.0, "last_error": "offline"},
            100.0,
        )
        self.assertEqual(set(services["svc"]), {"paths", "last_updated_at"})
        self.assertEqual(set(services["svc"]["paths"]), {"/A", "/B"})
        self.assertEqual(services["svc"]["last_updated_at"], 110.0)

        broken: dict[str, dict[str, object]] = {"svc": {"paths": "bad", "last_updated_at": "bad"}}
        instance.add_introspection_service_entry(broken, "svc", "/Recovered", {"status": "error"}, 120.0)
        self.assertEqual(broken["svc"]["last_updated_at"], 120.0)
        self.assertEqual(broken["svc"]["paths"]["/Recovered"]["status"], "unresponsive-backoff")
        zero_time: dict[str, dict[str, object]] = {"svc": {"paths": {}, "last_updated_at": "bad"}}
        instance.add_introspection_service_entry(
            zero_time,
            "svc",
            "/Zero",
            {"status": "error", "updated_at": 0.0},
            100.0,
        )
        self.assertEqual(zero_time["svc"]["last_updated_at"], 0.0)

    def test_services_snapshot_builds_all_valid_cached_services(self) -> None:
        instance = adapter(
            {
                "introspection:svc.one:/A": {"status": "fresh", "updated_at": 90.0, "value": "<node/>"},
                "introspection:svc.two:/B": {"status": "error", "updated_at": 95.0},
                "introspection::/Ignored": {"status": "error"},
                "path:svc/Other": {"status": "fresh"},
            }
        )
        services = instance.introspection_services_snapshot(100.0)
        self.assertEqual(set(services), {"svc.one", "svc.two"})
        self.assertEqual(services["svc.one"]["paths"]["/A"]["status"], "fresh")
        self.assertEqual(services["svc.one"]["last_updated_at"], 100.0)
        self.assertEqual(services["svc.two"]["paths"]["/B"]["status"], "unresponsive-backoff")

    def test_write_snapshot_emits_exact_payload_and_handles_disabled_or_failed_writes(self) -> None:
        instance = adapter()
        instance.introspection_services_snapshot = MagicMock(return_value={"svc": {"paths": {}}})
        write = MagicMock()
        with (
            patch.object(snapshot.time, "time", return_value=100.0),
            patch.object(snapshot.os, "getpid", return_value=1234),
            patch.object(snapshot, "write_text_atomically", write),
        ):
            instance.write_introspection_snapshot()
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], "/run/introspection.json")
        self.assertEqual(
            json.loads(write.call_args.args[1]),
            {
                "schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION,
                "captured_at": 100.0,
                "heartbeat_at": 100.0,
                "worker_state": "gateway",
                "writer_pid": 1234,
                "queue_depth": 3,
                "last_full_scan_at": 90.0,
                "services": {"svc": {"paths": {}}},
            },
        )
        instance.introspection_services_snapshot.assert_called_once_with(100.0)

        for enabled, path in ((False, "/run/map"), (True, "")):
            instance.dbus_introspection_enabled = enabled
            instance.dbus_introspection_snapshot_path = path
            with patch.object(snapshot, "write_text_atomically") as disabled_write:
                instance.write_introspection_snapshot()
            disabled_write.assert_not_called()

        instance.dbus_introspection_enabled = True
        instance.dbus_introspection_snapshot_path = "/run/map"
        for error in (OSError("readonly"), RuntimeError("runtime"), TypeError("type"), ValueError("value")):
            with (
                patch.object(snapshot, "write_text_atomically", side_effect=error),
                patch.object(snapshot.logging, "debug") as debug,
            ):
                instance.write_introspection_snapshot()
            debug.assert_called_once_with("Unable to write DBus introspection snapshot %s: %s", "/run/map", error)


if __name__ == "__main__":
    unittest.main()
