# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, Mapping

from venus_evcharger.core.contracts import CONTROL_API_EVENT_KINDS, normalized_control_api_event_fields
from venus_evcharger.control.http_api_command_contracts import ControlApiEventBusLike, ControlApiHttpService
from venus_evcharger.control.http_api_commands import _LocalControlApiCommand


class _LocalControlApiEvents(_LocalControlApiCommand):
    if TYPE_CHECKING:
        _service: ControlApiHttpService
        _RETRY_HEADER: str

        def _state_token(self) -> str: ...

    def _write_event_stream(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        event_bus = self._service._control_api_event_bus()
        limit = self._query_int(params, "limit", 20)
        after_seq = max(self._query_int(params, "after", 0), self._query_int(params, "resume", 0))
        timeout = self._query_float(params, "timeout", 5.0)
        heartbeat_interval = self._query_float(params, "heartbeat", 1.0)
        event_kinds = self._query_event_kinds(params)
        retry_ms = self._recommended_retry_ms(heartbeat_interval)
        once = self._query_bool(params, "once", False)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/x-ndjson")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header(self._RETRY_HEADER, str(retry_ms))
        handler.end_headers()
        last_seq = self._write_initial_event_snapshot(handler, after_seq, event_kinds, retry_ms)
        last_seq = self._write_recent_events(handler, event_bus, limit=limit, after_seq=last_seq, event_kinds=event_kinds)
        if not once:
            self._write_live_events(
                handler,
                event_bus,
                after_seq=last_seq,
                timeout=timeout,
                heartbeat_interval=heartbeat_interval,
                event_kinds=event_kinds,
                retry_ms=retry_ms,
            )

    def _write_initial_event_snapshot(
        self,
        handler: BaseHTTPRequestHandler,
        after_seq: int,
        event_kinds: frozenset[str],
        retry_ms: int,
    ) -> int:
        if after_seq > 0:
            return after_seq
        if event_kinds and "snapshot" not in event_kinds:
            return after_seq
        self._write_event_line(
            handler,
            normalized_control_api_event_fields(
                {
                    "seq": 0,
                    "api_version": "v1",
                    "kind": "snapshot",
                    "timestamp": time.time(),
                    "payload": {
                        **self._service._state_api_event_snapshot_payload(),
                        "state_token": self._state_token(),
                        "retry_hint_ms": retry_ms,
                    },
                }
            ),
        )
        return after_seq

    def _write_recent_events(
        self,
        handler: BaseHTTPRequestHandler,
        event_bus: ControlApiEventBusLike,
        *,
        limit: int,
        after_seq: int,
        event_kinds: frozenset[str],
    ) -> int:
        last_seq = after_seq
        for event in event_bus.recent(limit=limit, after_seq=after_seq):
            if not self._event_matches_kinds(event, event_kinds):
                last_seq = max(last_seq, int(event["seq"]))
                continue
            self._write_event_line(handler, event)
            last_seq = max(last_seq, int(event["seq"]))
        return last_seq

    def _write_live_events(
        self,
        handler: BaseHTTPRequestHandler,
        event_bus: ControlApiEventBusLike,
        *,
        after_seq: int,
        timeout: float,
        heartbeat_interval: float,
        event_kinds: frozenset[str],
        retry_ms: int,
    ) -> None:
        deadline = time.time() + max(0.0, timeout)
        last_seq = after_seq
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            event, last_seq = self._wait_for_matching_event(
                event_bus,
                after_seq=last_seq,
                timeout=self._event_wait_timeout(remaining, heartbeat_interval),
                event_kinds=event_kinds,
            )
            if event is None:
                if self._should_end_live_stream(remaining, heartbeat_interval):
                    return
                self._write_event_line(handler, self._heartbeat_event(last_seq, retry_ms))
                continue
            self._write_event_line(handler, event)
            last_seq = max(last_seq, int(event["seq"]))

    def _wait_for_matching_event(
        self,
        event_bus: ControlApiEventBusLike,
        *,
        after_seq: int,
        timeout: float,
        event_kinds: frozenset[str],
    ) -> tuple[Mapping[str, Any] | None, int]:
        deadline = time.time() + max(0.0, timeout)
        current_after_seq = after_seq
        while True:
            remaining = max(0.0, deadline - time.time())
            event = event_bus.wait_for_next(after_seq=current_after_seq, timeout=remaining)
            if event is None:
                return None, current_after_seq
            current_after_seq = max(current_after_seq, int(event["seq"]))
            if self._event_matches_kinds(event, event_kinds):
                return event, current_after_seq

    @staticmethod
    def _event_wait_timeout(remaining: float, heartbeat_interval: float) -> float:
        if heartbeat_interval <= 0.0:
            return remaining
        return min(remaining, heartbeat_interval)

    @staticmethod
    def _should_end_live_stream(remaining: float, heartbeat_interval: float) -> bool:
        return heartbeat_interval <= 0.0 or remaining <= 0.0

    @staticmethod
    def _heartbeat_event(after_seq: int, retry_ms: int) -> dict[str, Any]:
        return normalized_control_api_event_fields(
            {
                "seq": after_seq,
                "api_version": "v1",
                "kind": "HEARTBEAT",
                "timestamp": time.time(),
                "resume_token": str(after_seq),
                "payload": {
                    "alive": True,
                    "retry_hint_ms": retry_ms,
                    "resume_hint": str(after_seq),
                },
            }
        )

    @staticmethod
    def _query_event_kinds(params: dict[str, list[str]]) -> frozenset[str]:
        kinds: set[str] = set()
        for raw_value in params.get("kind", []):
            for item in raw_value.split(","):
                normalized = item.strip().lower()
                if normalized in CONTROL_API_EVENT_KINDS:
                    kinds.add(normalized)
        return frozenset(kinds)

    @staticmethod
    def _event_matches_kinds(event: Mapping[str, Any], event_kinds: frozenset[str]) -> bool:
        if not event_kinds:
            return True
        return str(event.get("kind", "")).strip().lower() in event_kinds

    @staticmethod
    def _recommended_retry_ms(heartbeat_interval: float) -> int:
        interval = heartbeat_interval if heartbeat_interval > 0.0 else 1.0
        return max(250, int(interval * 1000))

    @staticmethod
    def _query_int(params: dict[str, list[str]], key: str, default: int) -> int:
        values = params.get(key)
        if not values:
            return default
        try:
            return max(0, int(values[0]))
        except ValueError:
            return default

    @staticmethod
    def _query_float(params: dict[str, list[str]], key: str, default: float) -> float:
        values = params.get(key)
        if not values:
            return default
        try:
            return max(0.0, float(values[0]))
        except ValueError:
            return default

    @staticmethod
    def _query_bool(params: dict[str, list[str]], key: str, default: bool) -> bool:
        values = params.get(key)
        if not values:
            return default
        raw = values[0].strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _write_event_line(handler: BaseHTTPRequestHandler, event: Mapping[str, Any]) -> None:
        normalized_event = normalized_control_api_event_fields(event)
        handler.wfile.write((json.dumps(normalized_event, sort_keys=True) + "\n").encode())
        handler.wfile.flush()
