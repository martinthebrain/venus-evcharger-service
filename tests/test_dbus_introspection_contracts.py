#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the advisory DBus introspection cache boundary."""

from __future__ import annotations

import json
import builtins
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger import dbus_introspection as intro


class DbusIntrospectionContractTests(unittest.TestCase):
    def test_numeric_schema_and_freshness_boundaries_are_exact(self) -> None:
        self.assertIsNone(intro._optional_float(object()))
        self.assertIsNone(intro._optional_float("bad"))
        self.assertEqual(intro._optional_float(bytearray(b"2.5")), 2.5)
        self.assertEqual(intro._optional_int("3.9"), 3)
        self.assertIsNone(intro._optional_int(None))
        self.assertTrue(intro._snapshot_schema_valid({"schema_version": "1"}))
        self.assertFalse(intro._snapshot_schema_valid({"schema_version": 2}))

        payload = {"heartbeat_at": 95.0}
        self.assertTrue(intro._snapshot_fresh(payload, max_age_seconds=5.0, now=100.0))
        self.assertTrue(intro._snapshot_fresh({"captured_at": 100.0}, max_age_seconds=0.0, now=100.0))
        self.assertFalse(intro._snapshot_fresh({"captured_at": 99.5}, max_age_seconds=0.0, now=100.0))
        self.assertFalse(intro._snapshot_fresh(payload, max_age_seconds=4.999, now=100.0))
        self.assertFalse(intro._snapshot_fresh({"heartbeat_at": 101.0}, max_age_seconds=5.0, now=100.0))
        self.assertFalse(intro._snapshot_fresh({}, max_age_seconds=5.0, now=100.0))
        with patch.object(intro.time, "time", return_value=100.0) as clock:
            self.assertTrue(intro._snapshot_fresh(payload, max_age_seconds=5.0, now=None))
        clock.assert_called_once_with()

    def test_snapshot_loader_preserves_only_fresh_schema_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            payload = {"schema_version": 1, "heartbeat_at": 10.0, "services": {"svc": {}}}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(intro.load_introspection_snapshot(str(path), max_age_seconds=2.0, now=11.0), payload)
            self.assertEqual(intro._read_snapshot_payload(str(path)), payload)
            path.write_bytes(b"\xff")
            self.assertEqual(intro._read_snapshot_payload(str(path)), {})

        handle = MagicMock()
        handle.__enter__.return_value = handle
        with patch.object(builtins, "open", return_value=handle) as open_file, patch.object(
            intro.json, "load", return_value={"schema_version": 1}
        ):
            self.assertEqual(intro._read_snapshot_payload("/run/map"), {"schema_version": 1})
        open_file.assert_called_once_with("/run/map", encoding="utf-8")

    def test_path_findings_children_and_backoff_contracts(self) -> None:
        snapshot = {
            "services": {
                "svc": {
                    "paths": {
                        "/Fresh": {"status": "fresh", "children": ["A", 2, ""]},
                        "/Missing": {"status": "known-missing", "retry_after": 0},
                        "/Backoff": {"status": "unresponsive-backoff", "retry_after": 20},
                    }
                }
            }
        }
        self.assertEqual(intro.service_path_finding(snapshot, "svc", "/Fresh")["status"], "fresh")
        self.assertEqual(intro.path_children(snapshot, "svc", "/Fresh"), ["A", "2"])
        self.assertEqual(intro.path_children(snapshot, "svc", "/Missing"), [])
        self.assertEqual(intro._normalized_children(("A",)), [])
        self.assertEqual(intro.path_unusable_until(snapshot, "svc", "/Missing", now=10.0), (True, "known-missing"))
        self.assertEqual(intro.path_unusable_until(snapshot, "svc", "/Backoff", now=10.0), (True, "unresponsive-backoff"))
        self.assertEqual(intro.path_unusable_until(snapshot, "svc", "/Backoff", now=20.0), (False, ""))
        self.assertEqual(intro.path_unusable_until(snapshot, "svc", "/Fresh", now=10.0), (False, ""))
        stale = {"services": {"svc": {"paths": {"/P": {"status": "stale", "children": ["A"]}}}}}
        self.assertEqual(intro.path_children(stale, "svc", "/P"), [])
        self.assertFalse(intro._retry_after_pending({"retry_after": "bad"}, 10.0))
        self.assertFalse(intro._retry_after_pending({}, 0.0))
        with patch.object(intro.time, "time", return_value=10.0) as clock:
            self.assertTrue(intro._retry_after_pending({"retry_after": 11.0}, None))
        clock.assert_called_once_with()

        finding = MagicMock()
        finding.get.return_value = None
        with patch.object(intro, "service_path_finding", return_value=finding):
            self.assertEqual(intro.path_unusable_until({}, "svc", "/P", now=1.0), (False, ""))
        finding.get.assert_called_once_with("status")

        finding.reset_mock()
        finding.get.side_effect = ["fresh", ["A"]]
        with patch.object(intro, "service_path_finding", return_value=finding):
            self.assertEqual(intro.path_children({}, "svc", "/P"), ["A"])
        self.assertEqual(finding.get.call_args_list, [unittest.mock.call("status"), unittest.mock.call("children")])

    def test_owner_cache_reload_and_wrappers_preserve_arguments(self) -> None:
        owner = SimpleNamespace(
            dbus_introspection_snapshot_path=" /run/map.json ",
            dbus_introspection_max_age_seconds=12.0,
            _dbus_introspection_snapshot_loaded_at=5.0,
            _dbus_introspection_snapshot_cache={"cached": True},
        )
        self.assertEqual(intro._owner_snapshot_path(owner), "/run/map.json")
        self.assertEqual(intro._owner_snapshot_path(SimpleNamespace()), "")
        self.assertEqual(intro._owner_cached_snapshot(owner), {"cached": True})
        self.assertEqual(intro._owner_cached_snapshot(SimpleNamespace()), {})
        self.assertFalse(intro._owner_snapshot_reload_due(owner, 10.0))
        self.assertTrue(intro._owner_snapshot_reload_due(owner, 10.001))
        self.assertTrue(intro._owner_snapshot_reload_due(SimpleNamespace(), 5.001))

        with patch.object(intro, "_reload_owner_snapshot") as reload_snapshot:
            intro._refresh_owner_snapshot_if_due(owner, "/run/map.json", 10.0)
            reload_snapshot.assert_not_called()
            intro._refresh_owner_snapshot_if_due(owner, "/run/map.json", 11.0)
        reload_snapshot.assert_called_once_with(owner, "/run/map.json", 11.0)

        with patch.object(intro, "load_introspection_snapshot", return_value={"fresh": True}) as load:
            intro._reload_owner_snapshot(owner, "/run/map.json", 20.0)
        load.assert_called_once_with("/run/map.json", max_age_seconds=12.0, now=20.0)
        self.assertEqual(owner._dbus_introspection_snapshot_cache, {"fresh": True})
        self.assertEqual(owner._dbus_introspection_snapshot_loaded_at, 20.0)

        default_owner = SimpleNamespace()
        with patch.object(intro, "load_introspection_snapshot", return_value={}) as load:
            intro._reload_owner_snapshot(default_owner, "/run/default", 21.0)
        load.assert_called_once_with("/run/default", max_age_seconds=900.0, now=21.0)

        with patch.object(intro, "load_owner_introspection_snapshot", return_value={"snapshot": True}) as load_owner, patch.object(
            intro, "path_unusable_until", return_value=(True, "known-missing")
        ) as unusable:
            self.assertEqual(intro.owner_path_unusable(owner, "svc", "/P", now=30.0), (True, "known-missing"))
        load_owner.assert_called_once_with(owner, now=30.0)
        unusable.assert_called_once_with({"snapshot": True}, "svc", "/P", 30.0)

        with patch.object(intro, "load_owner_introspection_snapshot", return_value={"snapshot": True}) as load_owner, patch.object(
            intro, "path_children", return_value=["A"]
        ) as children:
            self.assertEqual(intro.owner_path_children(owner, "svc", "/P", now=31.0), ["A"])
        load_owner.assert_called_once_with(owner, now=31.0)
        children.assert_called_once_with({"snapshot": True}, "svc", "/P")

    def test_request_wrappers_normalize_and_preserve_payload(self) -> None:
        owner = SimpleNamespace(dbus_introspection_request_path=" /run/requests.json ")
        with patch.object(intro, "request_introspection", return_value=True) as request:
            self.assertTrue(
                intro.request_owner_introspection(owner, "svc", "/P", priority=7, reason="why", source="unit", now=12.0)
            )
        request.assert_called_once_with(
            "/run/requests.json", "svc", "/P", priority=7, reason="why", source="unit", now=12.0
        )
        with patch.object(intro, "request_introspection", return_value=True) as request:
            self.assertTrue(intro.request_owner_introspection(owner, "svc", "/Default"))
        request.assert_called_once_with(
            "/run/requests.json", "svc", "/Default", priority=100, reason="", source="", now=None
        )
        self.assertEqual(intro._request_target(" file ", " svc ", " /P "), ("file", "svc", "/P"))
        self.assertIsNone(intro._request_target("", "svc", "/P"))
        self.assertTrue(intro._valid_request_target("file", "svc", "/P"))
        self.assertFalse(intro._valid_request_target("file", "", "/P"))

        with patch.object(intro, "_request_target", return_value=("file", "svc", "/P")) as target, patch.object(
            intro, "_load_request_payload", return_value={}
        ) as load, patch.object(intro, "_append_request") as append, patch.object(
            intro, "_write_request_payload", return_value=True
        ) as write:
            self.assertTrue(intro.request_introspection(" file ", " svc ", " /P "))
        target.assert_called_once_with(" file ", " svc ", " /P ")
        load.assert_called_once_with("file")
        append.assert_called_once_with({}, "svc", "/P", priority=100, reason="", source="", now=None)
        write.assert_called_once_with("file", {})

        payload: dict[str, object] = {}
        intro._append_request(payload, "svc", "/P", priority=9, reason="reason", source="source", now=15.0)
        self.assertEqual(
            payload,
            {
                "requests": [
                    {
                        "service": "svc",
                        "path": "/P",
                        "priority": 9,
                        "reason": "reason",
                        "source": "source",
                        "requested_at": 15.0,
                    }
                ]
            },
        )
        with patch.object(intro.time, "time", return_value=16.0) as clock:
            intro._append_request(payload, "svc2", "/Q", priority=10, reason="", source="", now=None)
        clock.assert_called_once_with()
        requests = payload["requests"]
        self.assertIsInstance(requests, list)
        assert isinstance(requests, list)
        self.assertEqual(
            requests[1],
            {
                "service": "svc2",
                "path": "/Q",
                "priority": 10,
                "reason": "",
                "source": "",
                "requested_at": 16.0,
            },
        )

    def test_request_list_and_file_helpers_are_exact(self) -> None:
        original = [{"service": "a"}]
        payload: dict[str, object] = {"requests": original}
        copied = intro._request_list(payload)
        self.assertEqual(copied, original)
        self.assertIsNot(copied, original)
        self.assertIs(payload["requests"], copied)
        self.assertEqual(intro._request_list({"requests": {}}), [])
        empty_payload: dict[str, object] = {}
        self.assertEqual(intro._request_list(empty_payload), [])
        self.assertEqual(empty_payload, {"requests": []})

        with patch.object(intro, "compact_json", return_value="JSON") as compact, patch.object(
            intro, "write_text_atomically"
        ) as write:
            self.assertTrue(intro._write_request_payload("/run/request", {"requests": []}))
        compact.assert_called_once_with({"requests": []})
        write.assert_called_once_with("/run/request", "JSON")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "request.json"
            path.write_text('{"requests":[]}', encoding="utf-8")
            self.assertEqual(intro._load_request_payload(str(path)), {"requests": []})
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(intro._load_request_payload(str(path)), {})
        handle = MagicMock()
        handle.__enter__.return_value = handle
        with patch.object(builtins, "open", return_value=handle) as open_file, patch.object(
            intro.json, "load", return_value={"requests": []}
        ):
            self.assertEqual(intro._load_request_payload("/run/request"), {"requests": []})
        open_file.assert_called_once_with("/run/request", encoding="utf-8")
        self.assertEqual(intro._mapping_field({"value": {1: "x"}}, "value"), {"1": "x"})
        self.assertEqual(intro._object_mapping([]), {})


if __name__ == "__main__":
    unittest.main()
