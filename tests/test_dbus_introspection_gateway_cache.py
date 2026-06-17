# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger import dbus_introspection as intro


class DbusIntrospectionGatewayCacheTests(unittest.TestCase):
    def test_load_snapshot_rejects_unusable_payloads_and_ages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "map.json"

            self.assertEqual(intro._optional_float("bad"), None)
            self.assertEqual(intro.load_introspection_snapshot("", max_age_seconds=10.0), {})
            self.assertEqual(intro.load_introspection_snapshot(str(path), max_age_seconds=10.0), {})
            self.assertEqual(intro._load_request_payload(str(Path(temp_dir) / "missing-requests.json")), {})

            path.write_text("[]", encoding="utf-8")
            self.assertEqual(intro.load_introspection_snapshot(str(path), max_age_seconds=10.0), {})

            for payload in (
                {"schema_version": 999, "heartbeat_at": 10.0},
                {"schema_version": intro.DBUS_INTROSPECTION_SCHEMA_VERSION},
                {"schema_version": intro.DBUS_INTROSPECTION_SCHEMA_VERSION, "heartbeat_at": 20.0},
                {"schema_version": intro.DBUS_INTROSPECTION_SCHEMA_VERSION, "heartbeat_at": -10.0},
            ):
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(intro.load_introspection_snapshot(str(path), max_age_seconds=5.0, now=10.0), {})

            payload = {"schema_version": intro.DBUS_INTROSPECTION_SCHEMA_VERSION, "captured_at": 9.0}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(intro.load_introspection_snapshot(str(path), max_age_seconds=5.0, now=10.0), payload)

    def test_path_helpers_reject_malformed_findings(self) -> None:
        self.assertEqual(intro.service_path_finding({"services": []}, "svc", "/P"), {})
        self.assertEqual(intro.service_path_finding({"services": {"svc": []}}, "svc", "/P"), {})
        self.assertEqual(intro.service_path_finding({"services": {"svc": {"paths": []}}}, "svc", "/P"), {})
        self.assertEqual(intro.service_path_finding({"services": {"svc": {"paths": {"/P": []}}}}, "svc", "/P"), {})

        fresh = {"services": {"svc": {"paths": {"/P": {"status": "fresh", "children": ["A", "", None, 3]}}}}}
        self.assertEqual(intro.path_children(fresh, "svc", "/P"), ["A", "3"])
        self.assertEqual(
            intro.path_children({"services": {"svc": {"paths": {"/P": {"status": "fresh", "children": "bad"}}}}}, "svc", "/P"),
            [],
        )
        self.assertEqual(intro.path_unusable_until({}, "svc", "/P", now=10.0), (False, ""))
        self.assertEqual(
            intro.path_unusable_until(
                {"services": {"svc": {"paths": {"/P": {"status": "unresponsive-backoff", "retry_after": 20.0}}}}},
                "svc",
                "/P",
                now=10.0,
            ),
            (True, "unresponsive-backoff"),
        )
        self.assertEqual(
            intro.path_unusable_until(
                {"services": {"svc": {"paths": {"/P": {"status": "known-missing", "retry_after": 0.0}}}}},
                "svc",
                "/P",
                now=10.0,
            ),
            (True, "known-missing"),
        )

    def test_owner_cache_and_request_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "map.json"
            request_path = Path(temp_dir) / "requests.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": intro.DBUS_INTROSPECTION_SCHEMA_VERSION,
                        "heartbeat_at": 100.0,
                        "services": {"svc": {"paths": {"/P": {"status": "fresh", "children": ["C"]}}}},
                    }
                ),
                encoding="utf-8",
            )
            owner = SimpleNamespace(
                dbus_introspection_snapshot_path=str(snapshot_path),
                dbus_introspection_max_age_seconds=10.0,
                dbus_introspection_request_path=str(request_path),
            )

            self.assertEqual(intro.owner_path_children(owner, "svc", "/P", now=101.0), ["C"])
            snapshot_path.write_text("{", encoding="utf-8")
            self.assertEqual(intro.owner_path_children(owner, "svc", "/P", now=102.0), ["C"])
            self.assertEqual(intro.owner_path_children(owner, "svc", "/P", now=107.0), [])
            owner._dbus_introspection_snapshot_cache = []
            self.assertEqual(intro.load_owner_introspection_snapshot(owner, now=107.5), {})
            self.assertEqual(intro.load_owner_introspection_snapshot(SimpleNamespace(), now=1.0), {})
            self.assertEqual(intro.owner_path_unusable(owner, "svc", "/P", now=108.0), (False, ""))

            self.assertFalse(intro.request_owner_introspection(SimpleNamespace(), "svc", "/P"))
            self.assertFalse(intro.request_introspection("", "svc", "/P"))
            self.assertFalse(intro.request_introspection(str(request_path), "", "/P"))
            self.assertFalse(intro.request_introspection(str(request_path), "svc", ""))

            request_path.write_text(json.dumps({"requests": {}}), encoding="utf-8")
            self.assertTrue(
                intro.request_owner_introspection(owner, "svc", "/P", priority=7, reason="why", source="unit", now=123.0)
            )
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["requests"][0]["service"], "svc")
            self.assertEqual(payload["requests"][0]["priority"], 7)
            self.assertEqual(payload["requests"][0]["requested_at"], 123.0)

            with patch.object(intro, "write_text_atomically", side_effect=RuntimeError("full")):
                self.assertFalse(intro.request_introspection(str(request_path), "svc", "/P"))


if __name__ == "__main__":
    unittest.main()
