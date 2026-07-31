# SPDX-License-Identifier: GPL-3.0-or-later
"""Fast behavioral contracts for Control API event streaming."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from tests.control_api_http_cases_common import control_api_http_service
from venus_evcharger.control.http_api import LocalControlApiHttpServer
from venus_evcharger.control.http_api_events import (
    CONTROL_API_MAX_EVENT_HISTORY,
    CONTROL_API_MAX_EVENT_STREAM_SECONDS,
    CONTROL_API_MAX_HEARTBEAT_SECONDS,
)


class TestControlHttpEventsContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = MagicMock()
        self.service = control_api_http_service(
            event_bus=MagicMock(return_value=self.bus),
            event_snapshot_payload=MagicMock(return_value={"mode": 2}),
            state_token=MagicMock(return_value="state-token"),
            control_command_from_payload=MagicMock(),
            handle_control_command=MagicMock(),
        )
        self.server = LocalControlApiHttpServer(self.service, host="localhost", port=1)

    def test_stream_orchestration_has_exact_queries_headers_and_calls(self) -> None:
        handler = MagicMock()
        params = {
            "limit": ["7"],
            "after": ["3"],
            "resume": ["5"],
            "timeout": ["9.5"],
            "heartbeat": ["0.75"],
            "kind": ["state,command"],
            "once": ["0"],
        }
        with (
            patch.object(self.server.events, "write_initial_snapshot", return_value=5) as initial,
            patch.object(self.server.events, "write_recent_events", return_value=8) as recent,
            patch.object(self.server.events, "write_live_events") as live,
        ):
            self.server.events.write_stream(handler, params)
        self.service.event_bus.assert_called_once_with()
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(
            handler.send_header.call_args_list,
            [
                call("Content-Type", "application/x-ndjson"),
                call("Cache-Control", "no-cache"),
                call("X-Control-Api-Retry-Ms", "750"),
            ],
        )
        handler.end_headers.assert_called_once_with()
        kinds = frozenset({"state", "command"})
        initial.assert_called_once_with(handler, 5, kinds, 750)
        recent.assert_called_once_with(handler, self.bus, limit=7, after_seq=5, event_kinds=kinds)
        live.assert_called_once_with(
            handler,
            self.bus,
            after_seq=8,
            timeout=9.5,
            heartbeat_interval=0.75,
            event_kinds=kinds,
            retry_ms=750,
        )

    def test_once_stream_omits_live_follow(self) -> None:
        handler = MagicMock()
        with (
            patch.object(self.server.events, "write_initial_snapshot", return_value=0),
            patch.object(self.server.events, "write_recent_events", return_value=0),
            patch.object(self.server.events, "write_live_events") as live,
        ):
            self.server.events.write_stream(handler, {"once": ["true"]})
        live.assert_not_called()

    def test_stream_query_contract_uses_every_canonical_key_and_default(self) -> None:
        handler = MagicMock()
        with (
            patch.object(self.server.events, "query_int", side_effect=[20, 0, 0]) as query_int,
            patch.object(self.server.events, "query_float", side_effect=[5.0, 1.0]) as query_float,
            patch.object(self.server.events, "query_event_kinds", return_value=frozenset()) as query_kinds,
            patch.object(self.server.events, "recommended_retry_ms", return_value=1000) as retry,
            patch.object(self.server.events, "query_bool", return_value=True) as query_bool,
            patch.object(self.server.events, "write_initial_snapshot", return_value=0),
            patch.object(self.server.events, "write_recent_events", return_value=0),
        ):
            self.server.events.write_stream(handler, {})
        self.assertEqual(
            query_int.call_args_list,
            [
                call({}, "limit", 20, maximum=CONTROL_API_MAX_EVENT_HISTORY),
                call({}, "after", 0),
                call({}, "resume", 0),
            ],
        )
        self.assertEqual(
            query_float.call_args_list,
            [
                call({}, "timeout", 5.0, maximum=CONTROL_API_MAX_EVENT_STREAM_SECONDS),
                call({}, "heartbeat", 1.0, maximum=CONTROL_API_MAX_HEARTBEAT_SECONDS),
            ],
        )
        query_kinds.assert_called_once_with({})
        retry.assert_called_once_with(1.0)
        query_bool.assert_called_once_with({}, "once", False)

    def test_initial_snapshot_raw_contract_and_skip_rules_are_exact(self) -> None:
        handler = MagicMock()
        with patch.object(self.server.events, "write_event_line") as write, patch(
            "venus_evcharger.control.http_api_events.time.time", return_value=123.5
        ), patch(
            "venus_evcharger.control.http_api_events.normalized_control_api_event_fields",
            side_effect=lambda value: value,
        ) as normalize:
            self.assertEqual(self.server.events.write_initial_snapshot(handler, 0, frozenset(), 900), 0)
        expected = {
            "seq": 0,
            "api_version": "v1",
            "kind": "snapshot",
            "timestamp": 123.5,
            "payload": {"mode": 2, "state_token": "state-token", "retry_hint_ms": 900},
        }
        normalize.assert_called_once_with(expected)
        write.assert_called_once_with(handler, expected)
        self.service.event_snapshot_payload.assert_called_once_with()
        self.service.state_token.assert_called_once_with()

        with patch.object(self.server.events, "write_event_line") as write:
            self.assertEqual(self.server.events.write_initial_snapshot(handler, 7, frozenset(), 1), 7)
            self.assertEqual(self.server.events.write_initial_snapshot(handler, 1, frozenset(), 1), 1)
            self.assertEqual(
                self.server.events.write_initial_snapshot(handler, 0, frozenset({"command"}), 1),
                0,
            )
        write.assert_not_called()

        with patch.object(self.server.events, "write_event_line") as write:
            self.server.events.write_initial_snapshot(handler, 0, frozenset({"snapshot"}), 1)
        write.assert_called_once()

    def test_recent_events_advance_sequence_even_when_filtered(self) -> None:
        handler = MagicMock()
        events = [
            {"seq": 4, "kind": "command"},
            {"seq": 8, "kind": "state"},
            {"seq": 6, "kind": "command"},
        ]
        self.bus.recent.return_value = events
        with patch.object(self.server.events, "write_event_line") as write:
            self.assertEqual(
                self.server.events.write_recent_events(
                    handler, self.bus, limit=3, after_seq=2, event_kinds=frozenset({"state"})
                ),
                8,
            )
        self.bus.recent.assert_called_once_with(limit=3, after_seq=2)
        write.assert_called_once_with(handler, events[1])

    def test_wait_for_matching_event_skips_filtered_events_and_tracks_sequence(self) -> None:
        first = {"seq": 5, "kind": "command"}
        second = {"seq": 7, "kind": "state"}
        self.bus.wait_for_next.side_effect = [first, second]
        with patch("venus_evcharger.control.http_api_events.time.time", side_effect=[10.0, 10.25, 10.5]):
            result = self.server.events.wait_for_matching_event(
                self.bus, after_seq=3, timeout=2.0, event_kinds=frozenset({"state"})
            )
        self.assertEqual(result, (second, 7))
        self.assertEqual(
            self.bus.wait_for_next.call_args_list,
            [call(after_seq=3, timeout=1.75), call(after_seq=5, timeout=1.5)],
        )

        self.bus.wait_for_next.reset_mock()
        self.bus.wait_for_next.return_value = None
        self.bus.wait_for_next.side_effect = None
        with patch("venus_evcharger.control.http_api_events.time.time", side_effect=[20.0, 21.0]):
            self.assertEqual(
                self.server.events.wait_for_matching_event(
                    self.bus, after_seq=9, timeout=3.0, event_kinds=frozenset()
                ),
                (None, 9),
            )
        self.bus.wait_for_next.assert_called_once_with(after_seq=9, timeout=2.0)

        self.bus.wait_for_next.reset_mock()
        with patch("venus_evcharger.control.http_api_events.time.time", side_effect=[30.0, 30.25]):
            self.server.events.wait_for_matching_event(
                self.bus, after_seq=9, timeout=-2.0, event_kinds=frozenset()
            )
        self.bus.wait_for_next.assert_called_once_with(after_seq=9, timeout=0.0)

    def test_live_events_write_event_heartbeat_and_stop_paths(self) -> None:
        handler = MagicMock()
        first_event = {"seq": 5, "kind": "state"}
        second_event = {"seq": 6, "kind": "state"}
        with (
            patch(
                "venus_evcharger.control.http_api_events.time.time",
                side_effect=[0.0, 0.25, 0.5, 0.75, 1.0, 2.0],
            ),
            patch.object(
                self.server.events,
                "wait_for_matching_event",
                side_effect=[(first_event, 5), (second_event, 6)],
            ) as wait,
            patch.object(self.server.events, "write_event_line") as write,
        ):
            self.server.events.write_live_events(
                handler,
                self.bus,
                after_seq=2,
                timeout=1.5,
                heartbeat_interval=0.25,
                event_kinds=frozenset({"state"}),
                retry_ms=250,
            )
        self.assertEqual(
            wait.call_args_list,
            [
                call(self.bus, after_seq=2, timeout=0.25, event_kinds=frozenset({"state"})),
                call(self.bus, after_seq=5, timeout=0.25, event_kinds=frozenset({"state"})),
            ],
        )
        self.assertEqual(write.call_args_list, [call(handler, first_event), call(handler, second_event)])

        heartbeat = {"kind": "heartbeat"}
        with (
            patch(
                "venus_evcharger.control.http_api_events.time.time",
                side_effect=[0.0, 0.25, 0.5, 0.75, 1.0],
            ),
            patch.object(self.server.events, "wait_for_matching_event", return_value=(None, 4)),
            patch.object(self.server.events, "event_wait_timeout", return_value=0.2) as wait_timeout,
            patch.object(self.server.events, "should_end_live_stream", side_effect=[False, True]) as should_end,
            patch.object(self.server.events, "heartbeat_event", return_value=heartbeat) as heartbeat_factory,
            patch.object(self.server.events, "write_event_line") as write,
        ):
            self.server.events.write_live_events(
                handler,
                self.bus,
                after_seq=4,
                timeout=1.0,
                heartbeat_interval=0.25,
                event_kinds=frozenset(),
                retry_ms=300,
            )
        heartbeat_factory.assert_called_once_with(4, 300)
        write.assert_called_once_with(handler, heartbeat)
        self.assertEqual(wait_timeout.call_args_list, [call(0.5, 0.25), call(0.0, 0.25)])
        self.assertEqual(should_end.call_args_list, [call(0.5, 0.25), call(0.0, 0.25)])

        with (
            patch("venus_evcharger.control.http_api_events.time.time", side_effect=[10.0, 10.0]),
            patch.object(self.server.events, "wait_for_matching_event") as wait,
        ):
            self.server.events.write_live_events(
                handler,
                self.bus,
                after_seq=1,
                timeout=-1.0,
                heartbeat_interval=1.0,
                event_kinds=frozenset(),
                retry_ms=1000,
            )
        wait.assert_not_called()

    def test_event_helper_boundaries_are_exact(self) -> None:
        self.assertEqual(self.server.events.event_wait_timeout(3.0, 0.0), 3.0)
        self.assertEqual(self.server.events.event_wait_timeout(3.0, -1.0), 3.0)
        self.assertEqual(self.server.events.event_wait_timeout(3.0, 0.5), 0.5)
        self.assertEqual(self.server.events.event_wait_timeout(0.25, 0.5), 0.25)
        self.assertTrue(self.server.events.should_end_live_stream(3.0, 0.0))
        self.assertTrue(self.server.events.should_end_live_stream(0.0, 1.0))
        self.assertFalse(self.server.events.should_end_live_stream(0.1, 1.0))
        self.assertEqual(self.server.events.recommended_retry_ms(-1.0), 1000)
        self.assertEqual(self.server.events.recommended_retry_ms(0.0), 1000)
        self.assertEqual(self.server.events.recommended_retry_ms(0.1), 250)
        self.assertEqual(self.server.events.recommended_retry_ms(1.25), 1250)

    def test_query_and_filter_helpers_cover_defaults_normalization_and_invalid_values(self) -> None:
        self.assertEqual(
            self.server.events.query_event_kinds({"kind": [" STATE,command,unknown ", "snapshot"]}),
            frozenset({"state", "command", "snapshot"}),
        )
        self.assertEqual(self.server.events.query_event_kinds({}), frozenset())
        event = {"kind": " STATE "}
        self.assertTrue(self.server.events.event_matches_kinds(event, frozenset()))
        self.assertTrue(self.server.events.event_matches_kinds(event, frozenset({"state"})))
        self.assertFalse(self.server.events.event_matches_kinds(event, frozenset({"command"})))
        self.assertFalse(self.server.events.event_matches_kinds({}, frozenset({"none"})))
        self.assertFalse(self.server.events.event_matches_kinds({}, frozenset({"xxxx"})))
        self.assertEqual(self.server.events.query_int({}, "limit", 7), 7)
        self.assertEqual(self.server.events.query_int({"limit": ["-2"]}, "limit", 7), 0)
        self.assertEqual(self.server.events.query_int({"limit": ["bad"]}, "limit", 7), 7)
        self.assertEqual(self.server.events.query_int({"limit": ["99"]}, "limit", 7, maximum=8), 8)
        self.assertEqual(self.server.events.query_float({}, "timeout", 2.5), 2.5)
        self.assertEqual(self.server.events.query_float({"timeout": ["-2"]}, "timeout", 2.5), 0.0)
        self.assertEqual(self.server.events.query_float({"timeout": ["bad"]}, "timeout", 2.5), 2.5)
        self.assertEqual(
            self.server.events.query_float({"timeout": ["99"]}, "timeout", 2.5, maximum=3.0),
            3.0,
        )
        for raw in ("1", "true", "yes", "on", " TRUE "):
            self.assertTrue(self.server.events.query_bool({"once": [raw]}, "once", False), raw)
        for raw in ("0", "false", "no", "off", "unknown"):
            self.assertFalse(self.server.events.query_bool({"once": [raw]}, "once", True), raw)
        self.assertTrue(self.server.events.query_bool({}, "once", True))
        self.assertFalse(self.server.events.query_bool({}, "once", False))

    def test_heartbeat_and_line_serialization_pass_exact_contracts(self) -> None:
        raw = {
            "seq": 8,
            "api_version": "v1",
            "kind": "HEARTBEAT",
            "timestamp": 42.0,
            "resume_token": "8",
            "payload": {"alive": True, "retry_hint_ms": 500, "resume_hint": "8"},
        }
        with (
            patch("venus_evcharger.control.http_api_events.time.time", return_value=42.0),
            patch(
                "venus_evcharger.control.http_api_events.normalized_control_api_event_fields",
                side_effect=lambda value: value,
            ) as normalize,
        ):
            self.assertEqual(self.server.events.heartbeat_event(8, 500), raw)
        normalize.assert_called_once_with(raw)

        handler = SimpleNamespace(wfile=MagicMock())
        normalized = {"z": 2, "a": 1}
        with patch(
            "venus_evcharger.control.http_api_events.normalized_control_api_event_fields",
            return_value=normalized,
        ) as normalize:
            self.server.events.write_event_line(handler, {"z": 2})
        normalize.assert_called_once_with({"z": 2})
        handler.wfile.write.assert_called_once_with((json.dumps(normalized, sort_keys=True) + "\n").encode())
        handler.wfile.flush.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
