# SPDX-License-Identifier: GPL-3.0-or-later
import threading
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.control_api_http_cases_common import _FakeHandler
from venus_evcharger.control import (
    ControlApiAuditTrail,
    ControlApiEventBus,
    ControlApiIdempotencyStore,
    ControlCommand,
    ControlResult,
    LocalControlApiHttpServer,
)


class _ControlApiHttpStorageServerCases:
    def test_audit_trail_keeps_recent_entries_and_only_mirrors_runtime_paths(self) -> None:
        with patch("venus_evcharger.control.audit.open", create=True) as open_mock:
            trail = ControlApiAuditTrail(history_limit=2, path="/data/not-allowed.jsonl")
            trail.append({"command": {"name": "set_mode"}})
        open_mock.assert_not_called()

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

    def test_audit_trail_normalizes_non_mapping_payloads(self) -> None:
        trail = ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl")

        entry = trail.append({"command": "bad", "result": None, "error": object()})

        self.assertEqual(entry["command"], {})
        self.assertEqual(entry["result"], {})
        self.assertEqual(entry["error"], {})

    def test_audit_trail_logs_runtime_write_errors(self) -> None:
        with (
            patch("venus_evcharger.control.audit.open", side_effect=OSError("readonly"), create=True),
            patch("venus_evcharger.control.audit.logging.debug") as debug_log,
        ):
            ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl").append({"command": {"name": "set_mode"}})

        debug_log.assert_called_once()

    def test_idempotency_store_persists_only_to_runtime_paths_and_survives_restart(self) -> None:
        with patch("venus_evcharger.control.idempotency.open", create=True) as open_mock:
            store = ControlApiIdempotencyStore(history_limit=2, path="/data/not-allowed.json")
            store.put("idem-1", "fp", 200, {"ok": True})
        open_mock.assert_not_called()

        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/idempotency.json"
            first = ControlApiIdempotencyStore(history_limit=2, path=path)
            first.put("idem-1", "fp-1", 200, {"ok": True})
            first.put("idem-2", "fp-2", 202, {"ok": False})
            first.put("idem-3", "fp-3", 204, {"ok": "newest"})
            second = ControlApiIdempotencyStore(history_limit=2, path=path)

        self.assertEqual(second.count(), 2)
        self.assertEqual(second.path, path)
        self.assertIsNone(second.get("idem-1"))
        self.assertEqual(second.get("idem-2"), ("fp-2", 202, {"ok": False}))
        self.assertEqual(second.get("idem-3"), ("fp-3", 204, {"ok": "newest"}))

    def test_idempotency_store_ignores_invalid_runtime_payloads(self) -> None:
        with TemporaryDirectory() as tmpdir:
            invalid_json_path = f"{tmpdir}/invalid.json"
            with open(invalid_json_path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            invalid_json_store = ControlApiIdempotencyStore(history_limit=2, path=invalid_json_path)

            invalid_entries_path = f"{tmpdir}/entries.json"
            with open(invalid_entries_path, "w", encoding="utf-8") as handle:
                import json

                json.dump(
                    {
                        "bad-non-dict": "x",
                        "bad-empty-fingerprint": {"fingerprint": "", "status": 200, "response": {}},
                        "bad-response": {"fingerprint": "fp", "status": 200, "response": "x"},
                        "good": {"fingerprint": "fp-good", "status": 201, "response": {"ok": True}},
                    },
                    handle,
                )
            invalid_entries_store = ControlApiIdempotencyStore(history_limit=2, path=invalid_entries_path)

        self.assertEqual(invalid_json_store.count(), 0)
        self.assertEqual(invalid_entries_store.count(), 1)
        self.assertEqual(invalid_entries_store.get("good"), ("fp-good", 201, {"ok": True}))

    def test_idempotency_store_trims_loaded_runtime_entries_to_history_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/entries.json"
            with open(path, "w", encoding="utf-8") as handle:
                import json

                json.dump(
                    {
                        "idem-1": {"fingerprint": "fp-1", "status": 200, "response": {"ok": 1}},
                        "idem-2": {"fingerprint": "fp-2", "status": 201, "response": {"ok": 2}},
                        "idem-3": {"fingerprint": "fp-3", "status": 202, "response": {"ok": 3}},
                    },
                    handle,
                )
            store = ControlApiIdempotencyStore(history_limit=2, path=path)

        self.assertEqual(store.count(), 2)
        self.assertIsNone(store.get("idem-1"))
        self.assertEqual(store.get("idem-2"), ("fp-2", 201, {"ok": 2}))
        self.assertEqual(store.get("idem-3"), ("fp-3", 202, {"ok": 3}))

    def test_idempotency_store_logs_runtime_write_errors(self) -> None:
        with (
            patch("venus_evcharger.control.idempotency.open", side_effect=OSError("readonly"), create=True),
            patch("venus_evcharger.control.idempotency.logging.debug") as debug_log,
        ):
            ControlApiIdempotencyStore(history_limit=2, path="/run/control-idempotency.json").put(
                "idem-1",
                "fp",
                200,
                {"ok": True},
            )

        debug_log.assert_called_once()

    def test_start_initializes_server_and_background_thread(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
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
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server._server = MagicMock()

        with patch("venus_evcharger.control.http_api._ThreadingLocalControlHttpServer") as server_factory:
            server.start()

        server_factory.assert_not_called()

    def test_stop_handles_missing_server_and_missing_thread(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
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
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server.bound_host = "127.0.0.1"
        server.bound_port = 8765
        handler = _FakeHandler("/v1/control/health")

        server._handle_get(handler)
        payload = handler.json_payload()

        self.assertEqual(handler.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["transport"], "http")
        self.assertEqual(payload["listen_port"], 8765)

    def test_openapi_endpoint_returns_machine_readable_spec_without_auth(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")
        handler = _FakeHandler("/v1/openapi.json")

        server._handle_get(handler)
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
        self.assertIsNone(bus.wait_for_next(after_seq=99, timeout=0.0))

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
