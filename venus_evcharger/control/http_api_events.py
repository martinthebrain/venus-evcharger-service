# SPDX-License-Identifier: GPL-3.0-or-later
"""NDJSON event endpoint for Control API HTTP."""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler
from typing import Any, Mapping

from venus_evcharger.control.http_api_auth import ControlApiHttpAuthenticator
from venus_evcharger.control.http_api_command_contracts import (
    ControlApiEventBusPort,
    ControlApiEventsPort,
)
from venus_evcharger.core.contracts import CONTROL_API_EVENT_KINDS, normalized_control_api_event_fields


RETRY_HEADER = "X-Control-Api-Retry-Ms"
CONTROL_API_MAX_EVENT_HISTORY = 256
CONTROL_API_MAX_EVENT_STREAM_SECONDS = 30.0
CONTROL_API_MAX_HEARTBEAT_SECONDS = 10.0


class ControlApiHttpEventEndpoint:
    """Stream snapshots, recent events, heartbeats, and live events."""

    def __init__(self, service: ControlApiEventsPort, authenticator: ControlApiHttpAuthenticator) -> None:
        self._service = service
        self._authenticator = authenticator

    def write_stream(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        event_bus = self._service.event_bus()
        limit = self.query_int(params, "limit", 20, maximum=CONTROL_API_MAX_EVENT_HISTORY)
        after_seq = max(self.query_int(params, "after", 0), self.query_int(params, "resume", 0))
        timeout = self.query_float(
            params,
            "timeout",
            5.0,
            maximum=CONTROL_API_MAX_EVENT_STREAM_SECONDS,
        )
        heartbeat_interval = self.query_float(
            params,
            "heartbeat",
            1.0,
            maximum=CONTROL_API_MAX_HEARTBEAT_SECONDS,
        )
        event_kinds = self.query_event_kinds(params)
        retry_ms = self.recommended_retry_ms(heartbeat_interval)
        once = self.query_bool(params, "once", False)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/x-ndjson")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header(RETRY_HEADER, str(retry_ms))
        handler.end_headers()
        last_seq = self.write_initial_snapshot(handler, after_seq, event_kinds, retry_ms)
        last_seq = self.write_recent_events(
            handler,
            event_bus,
            limit=limit,
            after_seq=last_seq,
            event_kinds=event_kinds,
        )
        if not once:
            self.write_live_events(
                handler,
                event_bus,
                after_seq=last_seq,
                timeout=timeout,
                heartbeat_interval=heartbeat_interval,
                event_kinds=event_kinds,
                retry_ms=retry_ms,
            )

    def write_initial_snapshot(
        self,
        handler: BaseHTTPRequestHandler,
        after_seq: int,
        event_kinds: frozenset[str],
        retry_ms: int,
    ) -> int:
        if after_seq > 0 or (event_kinds and "snapshot" not in event_kinds):
            return after_seq
        self.write_event_line(
            handler,
            normalized_control_api_event_fields(
                {
                    "seq": 0,
                    "api_version": "v1",
                    "kind": "snapshot",
                    "timestamp": time.time(),
                    "payload": {
                        **self._service.event_snapshot_payload(),
                        "state_token": self._authenticator.state_token,
                        "retry_hint_ms": retry_ms,
                    },
                }
            ),
        )
        return after_seq

    def write_recent_events(
        self,
        handler: BaseHTTPRequestHandler,
        event_bus: ControlApiEventBusPort,
        *,
        limit: int,
        after_seq: int,
        event_kinds: frozenset[str],
    ) -> int:
        last_seq = after_seq
        for event in event_bus.recent(limit=limit, after_seq=after_seq):
            last_seq = max(last_seq, int(event["seq"]))
            if self.event_matches_kinds(event, event_kinds):
                self.write_event_line(handler, event)
        return last_seq

    def write_live_events(
        self,
        handler: BaseHTTPRequestHandler,
        event_bus: ControlApiEventBusPort,
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
            event, last_seq = self.wait_for_matching_event(
                event_bus,
                after_seq=last_seq,
                timeout=self.event_wait_timeout(remaining, heartbeat_interval),
                event_kinds=event_kinds,
            )
            if event is None:
                if self.should_end_live_stream(remaining, heartbeat_interval):
                    return
                self.write_event_line(handler, self.heartbeat_event(last_seq, retry_ms))
                continue
            self.write_event_line(handler, event)
            last_seq = max(last_seq, int(event["seq"]))

    def wait_for_matching_event(
        self,
        event_bus: ControlApiEventBusPort,
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
            if self.event_matches_kinds(event, event_kinds):
                return event, current_after_seq

    @staticmethod
    def event_wait_timeout(remaining: float, heartbeat_interval: float) -> float:
        return remaining if heartbeat_interval <= 0.0 else min(remaining, heartbeat_interval)

    @staticmethod
    def should_end_live_stream(remaining: float, heartbeat_interval: float) -> bool:
        return heartbeat_interval <= 0.0 or remaining <= 0.0

    @staticmethod
    def heartbeat_event(after_seq: int, retry_ms: int) -> dict[str, Any]:
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
    def query_event_kinds(params: dict[str, list[str]]) -> frozenset[str]:
        kinds = {
            item.strip().lower()
            for raw_value in params.get("kind", [])
            for item in raw_value.split(",")
            if item.strip().lower() in CONTROL_API_EVENT_KINDS
        }
        return frozenset(kinds)

    @staticmethod
    def event_matches_kinds(event: Mapping[str, Any], event_kinds: frozenset[str]) -> bool:
        return not event_kinds or str(event.get("kind", "")).strip().lower() in event_kinds

    @staticmethod
    def recommended_retry_ms(heartbeat_interval: float) -> int:
        interval = heartbeat_interval if heartbeat_interval > 0.0 else 1.0
        return max(250, int(interval * 1000))

    @staticmethod
    def query_int(
        params: dict[str, list[str]],
        key: str,
        default: int,
        *,
        maximum: int | None = None,
    ) -> int:
        values = params.get(key)
        if not values:
            return default
        try:
            value = max(0, int(values[0]))
        except ValueError:
            return default
        return min(value, maximum) if maximum is not None else value

    @staticmethod
    def query_float(
        params: dict[str, list[str]],
        key: str,
        default: float,
        *,
        maximum: float | None = None,
    ) -> float:
        values = params.get(key)
        if not values:
            return default
        try:
            value = max(0.0, float(values[0]))
        except ValueError:
            return default
        return min(value, maximum) if maximum is not None else value

    @staticmethod
    def query_bool(params: dict[str, list[str]], key: str, default: bool) -> bool:
        values = params.get(key)
        if not values:
            return default
        return values[0].strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def write_event_line(handler: BaseHTTPRequestHandler, event: Mapping[str, Any]) -> None:
        normalized_event = normalized_control_api_event_fields(event)
        handler.wfile.write((json.dumps(normalized_event, sort_keys=True) + "\n").encode())
        handler.wfile.flush()
