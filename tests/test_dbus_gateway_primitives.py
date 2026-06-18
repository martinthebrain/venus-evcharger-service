# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from venus_evcharger import dbus_gateway
from venus_evcharger import dbus_gateway_cache, dbus_gateway_commands
from venus_evcharger.dbus_gateway import (
    CacheValueMetadata,
    DbusCacheStore,
    DbusCommandInbox,
    GatewayClient,
    GatewayDbusServiceProxy,
    LatencyWindow,
    command_allowed_by_backpressure,
    command_queue_class,
    dbus_path_key,
    gateway_paths,
    gateway_value,
    read_json_file,
    write_json_file,
)


class DbusGatewayPrimitiveTests(unittest.TestCase):
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
        self.assertTrue(dbus_gateway_cache._value_is_stale("fresh", 20.0, 1.0))
        self.assertFalse(dbus_gateway_cache._valid_snapshot_payload({"captured_at": 0.0}))
        self.assertFalse(dbus_gateway_cache._valid_snapshot_payload([]))
        self.assertFalse(dbus_gateway_cache._snapshot_too_old(1.0, 100.0, -1.0))
        self.assertTrue(dbus_gateway_cache._snapshot_too_old(1.0, 100.0, 1.0))

        with tempfile.TemporaryDirectory() as temp_dir:
            store = DbusCacheStore(gateway_paths(str(Path(temp_dir) / "run")), stale_after_seconds=-1)
            self.assertEqual(store.stale_after_seconds, 0.0)
            with patch.object(dbus_gateway_cache, "_now", return_value=42.0):
                store.update_value("defaulted", 5)
            entry = store.snapshot(now=50.0)["values"]["defaulted"]
            self.assertEqual(entry["source"], "")
            self.assertEqual(entry["status"], "fresh")
            self.assertEqual(entry["confidence"], 1.0)
            self.assertEqual(entry["updated_at"], 42.0)

            store.mark_error("missing", source="svc/path", error=RuntimeError("boom"), now=60.0)
            error_entry = store.snapshot(now=60.0)["values"]["missing"]
            self.assertIsNone(error_entry["value"])
            self.assertEqual(error_entry["age_s"], 0.0)
            self.assertEqual(error_entry["last_error"], "boom")
            self.assertEqual(error_entry["confidence"], 0.0)

    def test_command_inbox_coalesce_ordering_and_error_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"kind": "set_value", "created_at": 1.0, "priority": "diagnostic", "coalesce_key": "k"})
            second = inbox.enqueue({"kind": "set_value", "created_at": 2.0, "priority": "diagnostic", "coalesce_key": "k"})
            self.assertEqual(first, second)
            pending_set_value = inbox.load_pending()[0][1]
            self.assertEqual(pending_set_value["created_at"], 1.0)
            self.assertEqual(pending_set_value["queue_class"], "remote-write")
            self.assertEqual(pending_set_value["lifecycle_state"], "coalesced")
            self.assertGreaterEqual(pending_set_value["updated_at"], 2.0)
            services_first = inbox.enqueue({"kind": "refresh_services", "created_at": 3.0})
            services_second = inbox.enqueue({"kind": "refresh_services", "created_at": 4.0})
            self.assertEqual(services_first, services_second)
            self.assertEqual(inbox.load_pending()[1][1]["coalesce_key"], "refresh:services")
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
            self.assertEqual(inbox.remove_coalesced(""), 0)
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

        with tempfile.TemporaryDirectory() as temp_dir:
            target = str(Path(temp_dir) / "coalesced.json")
            Path(target).write_text("{}", encoding="utf-8")
            self.assertFalse(dbus_gateway_commands._coalesced_target_exists({}, target))
            self.assertTrue(dbus_gateway_commands._coalesced_target_exists({"coalesce_key": " k "}, target))

        self.assertTrue(dbus_gateway_commands._replace_existing_coalesced([], {}))
        self.assertTrue(
            dbus_gateway_commands._same_priority(
                {"priority": "publish"},
                {"priority": "publish"},
            )
        )
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

        ordered = sorted(
            [
                {"kind": "register_path", "id": "path", "created_at": 2.0},
                {"kind": "register_service", "id": "service", "created_at": 3.0},
                {"kind": "publish_value", "priority": "publish", "path": "/Session/Time", "id": "time"},
                {"kind": "publish_value", "priority": "normal", "path": "/Session/Time", "id": "normal"},
            ],
            key=dbus_gateway_commands._command_order_key,
        )
        self.assertEqual([command["id"] for command in ordered], ["time", "normal", "service", "path"])
        self.assertEqual(dbus_gateway_commands._command_kind({"type": "set_value"}), "set_value")

    def test_gateway_client_socket_and_command_helpers(self) -> None:
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
            client.register_path("/Mode", 1, writeable=True)
            client.request_read("grid_power_w")
            client.request_read("svc", "/Path", reason="freshen")
            client.enqueue_command({"kind": "custom"})
            pending = DbusCommandInbox(paths.command_dir).load_pending()
            self.assertEqual(len(pending), 6)
            desired = [command for _path, command in pending if command.get("kind") == "publish_desired"][0]
            self.assertEqual(desired["paths"], {"/Ac/Power": 1200.0, "/Auto/Reason": "ok"})

            store = DbusCacheStore(paths)
            store.update_value("grid_power_w", 12.0, source="svc/path")
            store.health["backpressure"] = {"state": "slow"}
            store.write_snapshot_files()
            self.assertEqual(client.load_cache()["values"]["grid_power_w"]["value"], 12.0)
            self.assertEqual(client.load_health()["backpressure"]["state"], "slow")
            self.assertEqual(client.backpressure_state(), "slow")
            client.publish_path("/Auto/Reason", "optional")
            self.assertEqual(len(DbusCommandInbox(paths.command_dir).load_pending()), 6)
            client.publish_path("/Mode", 2, priority="user")
            pending = DbusCommandInbox(paths.command_dir).load_pending()
            self.assertEqual(len(pending), 6)
            mode = [
                command
                for _path, command in pending
                if command.get("path") == "/Mode" and command.get("kind") == "publish_value"
            ][0]
            self.assertEqual(mode["value"], 2)

    def test_command_queue_class_maps_gateway_workloads(self) -> None:
        self.assertLess(
            dbus_gateway.PRIORITY_VALUES["normal"],
            dbus_gateway.PRIORITY_VALUES["diagnostic"],
        )
        cases = [
            ({"kind": "register_path"}, "startup/register"),
            ({"kind": "register_service"}, "startup/register"),
            ({"kind": "publish_value", "path": "/Mode"}, "gui-critical-publish"),
            ({"kind": "publish_desired", "paths": {"/Session/Time": 1}}, "gui-critical-publish"),
            ({"kind": "publish_desired", "paths": {"/Auto/Reason": 1}}, "local-publish"),
            ({"kind": "publish_desired", "paths": ["/Mode"]}, "local-publish"),
            ({"kind": "publish_value", "path": "/Auto/Reason"}, "local-publish"),
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
        ]
        for command, state, expected in cases:
            with self.subTest(command=command, state=state):
                self.assertEqual(command_allowed_by_backpressure(command, state), expected)

    def test_gateway_proxy_gateway_value_and_latency_window(self) -> None:
        fake_client = MagicMock()
        proxy = GatewayDbusServiceProxy("svc", client=fake_client)
        callback = MagicMock(return_value=True)
        proxy.add_path("/Mode", 1, gettextcallback=object(), writeable=True, onchangecallback=callback)
        proxy.add_path("/Readonly", 0)
        proxy.register()
        self.assertEqual(proxy["/Mode"], 1)
        proxy["/Mode"] = 2
        self.assertEqual(proxy["/Mode"], 2)
        proxy.publish_paths({"/Mode": 5, "/Ac/Power": 1200.0, "": "ignored"})
        self.assertEqual(proxy["/Mode"], 5)
        self.assertEqual(proxy["/Ac/Power"], 1200.0)
        self.assertTrue(proxy.apply_gateway_write("/Mode", 3))
        callback.assert_called_once_with("/Mode", 3)
        self.assertTrue(proxy.apply_gateway_write("/Other", 4))
        self.assertEqual(proxy["/Other"], 4)
        fake_client.register_path.assert_any_call("/Mode", 1, writeable=True)
        fake_client.register_path.assert_any_call("/Readonly", 0, writeable=False)
        fake_client.publish_path.assert_called_once_with("/Mode", 2)
        fake_client.publish_paths.assert_called_once_with({"/Mode": 5, "/Ac/Power": 1200.0})
        fake_client.enqueue_command.assert_called_once()

        snapshot = {
            "values": {
                "fresh": {"status": "fresh", "age_s": 1.0, "value": 1},
                "stale": {"status": "stale", "age_s": 2.0, "value": 2},
                "error": {"status": "error", "age_s": 0.0, "value": 3},
            }
        }
        self.assertEqual(gateway_value(snapshot, "fresh", max_age_seconds=5.0), 1)
        self.assertEqual(gateway_value(snapshot, "stale", max_age_seconds=5.0), 2)
        self.assertIsNone(gateway_value(snapshot, "missing", max_age_seconds=5.0))
        self.assertIsNone(gateway_value(snapshot, "error", max_age_seconds=5.0))
        self.assertIsNone(gateway_value(snapshot, "stale", max_age_seconds=1.0))
        self.assertEqual(dbus_path_key("svc", "/P"), "path:svc/P")

        window = LatencyWindow(window_seconds=10.0)
        window.record_latency(-1.0, now=0.0)
        window.record_latency(20.0, now=5.0)
        window.record_timeout(now=5.0)
        self.assertEqual(window.summary(now=5.0)["max_latency_ms"], 20.0)
        window.record_latency(30.0, now=20.0)
        summary = window.summary(now=20.0)
        self.assertEqual(summary["timeouts_60s"], 0)
        self.assertEqual(summary["avg_latency_ms"], 30.0)


if __name__ == "__main__":
    unittest.main()
