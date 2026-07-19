# SPDX-License-Identifier: GPL-3.0-or-later
import json
import threading
import time
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, mock_open, patch

from tests.control_api_http_cases_common import _FakeHandler, control_api_http_service
from venus_evcharger.control import (
    ControlApiAuditTrail,
    ControlApiEventBus,
    LocalControlApiHttpServer,
)


class _ControlApiHttpStorageServerCases:
    def test_audit_trail_keeps_recent_entries_and_only_mirrors_runtime_paths(self) -> None:
        with (
            patch("venus_evcharger.control.audit.open", create=True) as open_mock,
            patch("venus_evcharger.control.audit.os.makedirs") as makedirs_mock,
        ):
            trail = ControlApiAuditTrail(history_limit=2, path="/data/not-allowed.jsonl")
            trail.append({"command": {"name": "set_mode"}})
        open_mock.assert_not_called()
        makedirs_mock.assert_not_called()

        with patch("venus_evcharger.control.audit.open", create=True) as open_mock:
            trail = ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl")
            first = trail.append({"command": {"name": "set_mode"}})
            second = trail.append({"command": {"name": "set_auto_start"}})
            third = trail.append({"command": {"name": "set_enable"}})

        self.assertEqual(first["seq"], 1)
        self.assertEqual(third["seq"], 3)
        self.assertEqual(trail.count(), 2)
        self.assertEqual(trail.path, "/run/control-audit.jsonl")
        self.assertEqual([entry["command"]["name"] for entry in trail.recent(limit=5)], ["set_auto_start", "set_enable"])
        open_mock.assert_called()

    def test_audit_trail_default_path_and_history_contracts_are_exact(self) -> None:
        trail = ControlApiAuditTrail()
        self.assertEqual(trail.path, "")

        for index in range(201):
            trail.append({"timestamp": index, "command": {"index": index}})

        self.assertEqual(trail.count(), 200)
        self.assertEqual(trail.recent(limit=1)[0]["seq"], 201)
        self.assertEqual(trail.recent(limit=200)[0]["seq"], 2)

        single_entry_trail = ControlApiAuditTrail(history_limit=1)
        first = single_entry_trail.append({"timestamp": 1.0, "command": {"name": "old"}})
        second = single_entry_trail.append({"timestamp": 2.0, "command": {"name": "new"}})

        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 2)
        self.assertEqual(single_entry_trail.count(), 1)
        self.assertEqual(single_entry_trail.recent(limit=2), [second])

    def test_audit_trail_normalizes_all_fields_and_returns_copies(self) -> None:
        trail = ControlApiAuditTrail(history_limit=2)

        entry = trail.append(
            {
                "timestamp": "12.5",
                "transport": "unix",
                "scope": "admin",
                "client_host": 42,
                "status_code": "202",
                "replayed": 1,
                "command": {"name": "set_mode"},
                "result": {"status": "accepted"},
                "error": {"code": "none"},
            }
        )
        returned = trail.recent(limit=1)[0]
        returned["command"]["name"] = "changed-copy"

        self.assertEqual(
            entry,
            {
                "seq": 1,
                "timestamp": 12.5,
                "transport": "unix",
                "scope": "admin",
                "client_host": "42",
                "status_code": 202,
                "replayed": True,
                "command": {"name": "set_mode"},
                "result": {"status": "accepted"},
                "error": {"code": "none"},
            },
        )
        self.assertEqual(trail.recent(limit=1)[0]["command"], {"name": "set_mode"})

    def test_audit_trail_default_and_blank_field_fallbacks_are_exact(self) -> None:
        trail = ControlApiAuditTrail(history_limit=2)

        with patch("venus_evcharger.control.audit.time.time", return_value=88.5):
            missing_entry = trail.append({"command": {"name": "missing-defaults"}})

        self.assertEqual(missing_entry["timestamp"], 88.5)
        self.assertEqual(missing_entry["transport"], "http")
        self.assertEqual(missing_entry["scope"], "control")
        self.assertEqual(missing_entry["client_host"], "")
        self.assertEqual(missing_entry["status_code"], 0)
        self.assertFalse(missing_entry["replayed"])

        with patch("venus_evcharger.control.audit.time.time", return_value=99.25):
            entry = trail.append(
                {
                    "transport": "",
                    "scope": "",
                    "client_host": None,
                    "status_code": "",
                    "replayed": "",
                    "command": "bad",
                    "result": None,
                    "error": object(),
                }
            )

        self.assertEqual(entry["timestamp"], 99.25)
        self.assertEqual(entry["transport"], "http")
        self.assertEqual(entry["scope"], "control")
        self.assertEqual(entry["client_host"], "")
        self.assertEqual(entry["status_code"], 0)
        self.assertFalse(entry["replayed"])
        self.assertEqual(entry["command"], {})
        self.assertEqual(entry["result"], {})
        self.assertEqual(entry["error"], {})

    def test_audit_trail_normalizes_non_mapping_payloads(self) -> None:
        trail = ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl")

        entry = trail.append({"command": "bad", "result": None, "error": object()})

        self.assertEqual(entry["command"], {})
        self.assertEqual(entry["result"], {})
        self.assertEqual(entry["error"], {})

    def test_audit_trail_recent_limit_and_copy_contracts_are_exact(self) -> None:
        trail = ControlApiAuditTrail(history_limit=3)
        trail.append({"timestamp": 1.0, "command": {"name": "one"}})
        second = trail.append({"timestamp": 2.0, "command": {"name": "two"}})
        third = trail.append({"timestamp": 3.0, "command": {"name": "three"}})

        self.assertEqual(trail.recent(limit=0), [])
        self.assertEqual(trail.recent(limit=-1), [])
        self.assertEqual(trail.recent(limit=2), [second, third])

        recent = trail.recent(limit=1)
        recent[0]["command"]["name"] = "changed-copy"
        self.assertEqual(trail.recent(limit=1)[0]["command"], {"name": "three"})

    def test_audit_trail_recent_default_limit_is_twenty_entries(self) -> None:
        trail = ControlApiAuditTrail(history_limit=25)

        for index in range(21):
            trail.append({"timestamp": index, "command": {"index": index}})

        recent = trail.recent()
        self.assertEqual(len(recent), 20)
        self.assertEqual(recent[0]["seq"], 2)
        self.assertEqual(recent[-1]["seq"], 21)

    def test_audit_trail_runtime_log_writes_exact_jsonl_contract(self) -> None:
        file_handle = mock_open()
        with (
            patch("venus_evcharger.control.audit.os.makedirs") as makedirs_mock,
            patch("venus_evcharger.control.audit.open", file_handle, create=True) as open_mock,
        ):
            trail = ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl")
            entry = trail.append({"timestamp": 1.0, "command": {"z": 1, "a": 2}})

        makedirs_mock.assert_called_once_with("/run", exist_ok=True)
        open_mock.assert_called_once_with("/run/control-audit.jsonl", "a", encoding="utf-8")
        file_handle().write.assert_called_once_with(json.dumps(entry, sort_keys=True) + "\n")

    def test_audit_trail_runtime_log_keeps_sorted_jsonl_on_disk(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/control-audit.jsonl"
            trail = ControlApiAuditTrail(history_limit=2, path=path)
            trail.append({"timestamp": 1.0, "command": {"z": 1, "a": 2}})

            with open(path, "r", encoding="utf-8") as handle:
                persisted = handle.read()

        self.assertTrue(persisted.endswith("\n"))
        self.assertLess(persisted.index('"client_host"'), persisted.index('"command"'))
        self.assertIn('"command": {"a": 2, "z": 1}', persisted)
        self.assertIn('"timestamp": 1.0', persisted)

    def test_audit_trail_logs_runtime_write_errors(self) -> None:
        with (
            patch("venus_evcharger.control.audit.open", side_effect=OSError("readonly"), create=True),
            patch("venus_evcharger.control.audit.logging.debug") as debug_log,
        ):
            ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl").append({"command": {"name": "set_mode"}})

        debug_log.assert_called_once()
        self.assertEqual(debug_log.call_args.args[0], "Unable to append Control API audit log %s: %s")
        self.assertEqual(debug_log.call_args.args[1], "/run/control-audit.jsonl")
        self.assertIsInstance(debug_log.call_args.args[2], OSError)

    def test_start_initializes_server_and_background_thread(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        fake_server = MagicMock()
        fake_server.server_address = ("127.0.0.1", 8765)
        fake_thread = MagicMock()

        with (
            patch("venus_evcharger.control.http_api._ThreadingLocalControlHttpServer", return_value=fake_server) as server_factory,
            patch("venus_evcharger.control.http_api.threading.Thread", return_value=fake_thread) as thread_factory,
        ):
            server.start()
            self.assertEqual(server.bound_host, "127.0.0.1")
            self.assertEqual(server.bound_port, 8765)
            server.stop()

        server_factory.assert_called_once()
        thread_factory.assert_called_once()
        fake_thread.start.assert_called_once_with()
        fake_thread.join.assert_called_once_with(timeout=1.0)

    def test_start_is_noop_when_server_is_already_running(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server._server = MagicMock()

        with patch("venus_evcharger.control.http_api._ThreadingLocalControlHttpServer") as server_factory:
            server.start()

        server_factory.assert_not_called()

    def test_stop_handles_missing_server_and_missing_thread(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server.stop()

        fake_server = MagicMock()
        server._server = fake_server
        server._thread = None

        server.stop()

        fake_server.shutdown.assert_called_once_with()
        fake_server.server_close.assert_called_once_with()

    def test_health_endpoint_reports_bound_local_server(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server.bound_host = "127.0.0.1"
        server.bound_port = 8765
        handler = _FakeHandler("/v1/control/health")

        server.router.handle_get(handler)
        payload = handler.json_payload()

        self.assertEqual(handler.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["transport"], "http")
        self.assertEqual(payload["listen_port"], 8765)

    def test_openapi_endpoint_returns_machine_readable_spec_without_auth(self) -> None:
        service = control_api_http_service(
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")
        handler = _FakeHandler("/v1/openapi.json")

        server.router.handle_get(handler)
        payload = handler.json_payload()

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertIn("/v1/control/command", payload["paths"])
        self.assertIn("ControlCommandResponse", payload["components"]["schemas"])

    def test_event_bus_publish_recent_and_wait_cover_immediate_and_timeout_paths(self) -> None:
        bus = ControlApiEventBus(history_limit=2)

        first = bus.publish("command", {"detail": "one"})
        second = bus.publish("state", {"detail": "two"})

        self.assertEqual(bus.recent(limit=0), [])
        self.assertEqual(bus.recent(limit=5, after_seq=first["seq"])[0]["seq"], second["seq"])
        self.assertEqual(bus.wait_for_next(after_seq=0, timeout=0.0)["seq"], first["seq"])
        with patch.object(bus._condition, "wait", return_value=False) as wait_mock:
            self.assertIsNone(bus.wait_for_next(after_seq=99, timeout=0.0))
        wait_mock.assert_called_once_with(timeout=0.0)

    def test_event_bus_publish_shape_history_and_copy_contracts_are_exact(self) -> None:
        bus = ControlApiEventBus(history_limit=2)
        payload = {"z": 1, "a": 2}

        with patch("venus_evcharger.control.events.time.time", return_value=123.5):
            first = bus.publish("command", payload)
            second = bus.publish("state", {"detail": "two"})
            third = bus.publish("diagnostic", {"detail": "three"})

        self.assertEqual(
            first,
            {
                "seq": 1,
                "api_version": "v1",
                "kind": "command",
                "timestamp": 123.5,
                "resume_token": "1",
                "payload": {"z": 1, "a": 2},
            },
        )
        payload["z"] = "changed-after-publish"
        first["payload"]["a"] = "changed-after-return"

        self.assertEqual(second["seq"], 2)
        self.assertEqual(third["seq"], 3)
        self.assertEqual(bus.recent(limit=5), [second, third])

        recent = bus.recent(limit=1)
        recent[0]["payload"]["detail"] = "changed-copy"
        self.assertEqual(bus.recent(limit=1)[0]["payload"], {"detail": "three"})

    def test_event_bus_default_history_and_recent_limit_are_exact(self) -> None:
        bus = ControlApiEventBus()

        for index in range(52):
            bus.publish("state", {"index": index})

        self.assertEqual(bus.recent(limit=51)[0]["seq"], 3)
        self.assertEqual(bus.recent(limit=51)[-1]["seq"], 52)

        default_recent = bus.recent()
        self.assertEqual(len(default_recent), 20)
        self.assertEqual(default_recent[0]["seq"], 33)
        self.assertEqual(default_recent[-1]["seq"], 52)

        one_event_bus = ControlApiEventBus()
        only = one_event_bus.publish("state", {"index": 1})
        self.assertEqual(one_event_bus.recent(), [only])

        single_entry_bus = ControlApiEventBus(history_limit=1)
        first = single_entry_bus.publish("state", {"index": 1})
        second = single_entry_bus.publish("state", {"index": 2})
        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 2)
        self.assertEqual(single_entry_bus.recent(limit=5), [second])

    def test_event_bus_after_seq_boundaries_are_strictly_greater_than(self) -> None:
        bus = ControlApiEventBus(history_limit=3)
        first = bus.publish("state", {"index": 1})
        second = bus.publish("state", {"index": 2})

        self.assertEqual(bus.recent(limit=5, after_seq=first["seq"]), [second])
        self.assertEqual(bus.wait_for_next(after_seq=first["seq"], timeout=0.0), second)
        with patch.object(bus._condition, "wait", return_value=False) as wait_mock:
            self.assertIsNone(bus.wait_for_next(after_seq=second["seq"], timeout=0.0))
        wait_mock.assert_called_once_with(timeout=0.0)

    def test_event_bus_wait_default_returns_ready_event_without_waiting(self) -> None:
        bus = ControlApiEventBus(history_limit=2)
        first = bus.publish("state", {"index": 1})

        with patch.object(bus._condition, "wait", return_value=False) as wait_mock:
            self.assertEqual(bus.wait_for_next(), first)

        wait_mock.assert_not_called()

    def test_event_bus_wait_default_timeout_is_thirty_seconds_without_ready_event(self) -> None:
        bus = ControlApiEventBus(history_limit=2)

        with patch.object(bus._condition, "wait", return_value=False) as wait_mock:
            self.assertIsNone(bus.wait_for_next())

        wait_mock.assert_called_once_with(timeout=30.0)

    def test_event_bus_wait_uses_clamped_timeout_when_no_event_is_ready(self) -> None:
        bus = ControlApiEventBus(history_limit=2)

        with patch.object(bus._condition, "wait", return_value=False) as wait_mock:
            self.assertIsNone(bus.wait_for_next(after_seq=99, timeout=-1.0))

        wait_mock.assert_called_once_with(timeout=0.0)

    def test_event_bus_wait_for_next_returns_event_after_wait(self) -> None:
        bus = ControlApiEventBus(history_limit=2)

        def _publish_later() -> None:
            time.sleep(0.01)
            bus.publish("state", {"detail": "later"})

        thread = threading.Thread(target=_publish_later)
        thread.start()
        try:
            event = bus.wait_for_next(after_seq=0, timeout=0.2)
        finally:
            thread.join(timeout=1.0)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["kind"], "state")
