# SPDX-License-Identifier: GPL-3.0-or-later
"""Tiny stdlib client for the local Control and State API."""

from __future__ import annotations

import http.client
import json
import socket
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode, urlsplit


class _UnixSocketHttpConnection(http.client.HTTPConnection):
    """Send one HTTP/1.1 request over one local unix socket."""

    def __init__(self, unix_socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._unix_socket_path = unix_socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._unix_socket_path)


@dataclass(frozen=True, slots=True)
class ControlApiClientResponse:
    """Normalized HTTP response returned by the local API client."""

    status: int
    headers: dict[str, str]
    body: str

    def json(self) -> dict[str, Any]:
        parsed = json.loads(self.body or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("Response body is not one JSON object.")
        return parsed

    def ndjson(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in self.body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict):
                raise ValueError("NDJSON event line is not one JSON object.")
            events.append(parsed)
        return events


class LocalControlApiClient:
    """Call the local HTTP or unix-socket Control API without external deps."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8765",
        unix_socket_path: str = "",
        bearer_token: str = "",
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._unix_socket_path = unix_socket_path.strip()
        self._bearer_token = bearer_token.strip()
        self._timeout = float(timeout)

    def get(
        self,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ControlApiClientResponse:
        return self._request("GET", path, query=query, headers=headers)

    def state(self, state_name: str, *, headers: Mapping[str, str] | None = None) -> ControlApiClientResponse:
        normalized = state_name.strip().lower()
        if not normalized.startswith("/v1/"):
            normalized = f"/v1/state/{normalized}"
        return self.get(normalized, headers=headers)

    def capabilities(self, *, headers: Mapping[str, str] | None = None) -> ControlApiClientResponse:
        return self.get("/v1/capabilities", headers=headers)

    def automation(self, *, headers: Mapping[str, str] | None = None) -> ControlApiClientResponse:
        return self.state("automation", headers=headers)

    def safe_command(
        self,
        payload: Mapping[str, Any],
        *,
        state_endpoint: str = "automation",
        idempotency_key: str = "",
        command_id: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> ControlApiClientResponse:
        state_response = self.state(state_endpoint, headers=headers)
        state_token = self.state_token_from_response(state_response)
        if not state_token:
            raise ValueError(f"State endpoint '{state_endpoint}' did not return a state token.")
        return self.command(
            payload,
            idempotency_key=idempotency_key,
            command_id=command_id,
            if_match=state_token,
            headers=headers,
        )

    def health(self, *, headers: Mapping[str, str] | None = None) -> ControlApiClientResponse:
        return self.get("/v1/control/health", headers=headers)

    def openapi(self, *, headers: Mapping[str, str] | None = None) -> ControlApiClientResponse:
        return self.get("/v1/openapi.json", headers=headers)

    def events(
        self,
        *,
        limit: int = 20,
        after: int | None = None,
        resume: int | None = None,
        timeout: float = 5.0,
        heartbeat: float = 1.0,
        kinds: Sequence[str] = (),
        once: bool = False,
        headers: Mapping[str, str] | None = None,
    ) -> ControlApiClientResponse:
        return self.get(
            "/v1/events",
            query=self._event_query(
                limit=limit,
                after=after,
                resume=resume,
                timeout=timeout,
                heartbeat=heartbeat,
                kinds=kinds,
                once=once,
            ),
            headers=headers,
        )

    def command(
        self,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str = "",
        command_id: str = "",
        if_match: str = "",
        state_token: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> ControlApiClientResponse:
        return self._request(
            "POST",
            "/v1/control/command",
            json_payload=dict(payload),
            headers=self._command_headers(
                headers=headers,
                idempotency_key=idempotency_key,
                command_id=command_id,
                if_match=if_match,
                state_token=state_token,
            ),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ControlApiClientResponse:
        request_target = self._request_target(path, query=query)
        request_headers = self._request_headers(headers, json_payload=json_payload)
        body = self._request_body(json_payload)
        connection = self._connection()
        try:
            connection.request(method, request_target, body=body, headers=request_headers)
            response = connection.getresponse()
            response_body = response.read().decode()
            return ControlApiClientResponse(
                status=int(response.status),
                headers={key: value for key, value in response.getheaders()},
                body=response_body,
            )
        finally:
            connection.close()

    @staticmethod
    def _event_query(
        *,
        limit: int,
        after: int | None,
        resume: int | None,
        timeout: float,
        heartbeat: float,
        kinds: Sequence[str],
        once: bool,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
            "heartbeat": heartbeat,
            "once": int(bool(once)),
        }
        LocalControlApiClient._set_optional_query_value(query, "after", after)
        LocalControlApiClient._set_optional_query_value(query, "resume", resume)
        LocalControlApiClient._set_optional_query_value(query, "kind", LocalControlApiClient._joined_kinds(kinds))
        return query

    @staticmethod
    def _set_optional_query_value(query: dict[str, Any], key: str, value: Any) -> None:
        if value not in (None, ""):
            query[key] = value

    @staticmethod
    def _joined_kinds(kinds: Sequence[str]) -> str:
        return ",".join(kind.strip() for kind in kinds if kind.strip())

    @staticmethod
    def _command_headers(
        *,
        headers: Mapping[str, str] | None,
        idempotency_key: str,
        command_id: str,
        if_match: str,
        state_token: str,
    ) -> dict[str, str]:
        merged_headers = dict(headers or {})
        header_values = {
            "Idempotency-Key": idempotency_key,
            "X-Command-Id": command_id,
            "If-Match": if_match,
            "X-State-Token": state_token,
        }
        for key, value in header_values.items():
            if value:
                merged_headers[key] = value
        return merged_headers

    def _connection(self) -> http.client.HTTPConnection:
        if self._unix_socket_path:
            return _UnixSocketHttpConnection(self._unix_socket_path, timeout=self._timeout)
        parts = urlsplit(self._base_url)
        host = parts.hostname or "127.0.0.1"
        port = int(parts.port or (443 if parts.scheme == "https" else 80))
        return http.client.HTTPConnection(host, port, timeout=self._timeout)

    def _request_target(self, path: str, *, query: Mapping[str, Any] | None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        if not query:
            return normalized_path
        return f"{normalized_path}?{urlencode(self._normalized_query_items(query))}"

    @staticmethod
    def _normalized_query_items(query: Mapping[str, Any]) -> list[tuple[str, Any]]:
        items: list[tuple[str, Any]] = []
        for key, value in query.items():
            if isinstance(value, (list, tuple)):
                items.extend((key, item) for item in value)
                continue
            items.append((key, value))
        return items

    def _request_headers(
        self,
        headers: Mapping[str, str] | None,
        *,
        json_payload: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        request_headers = {"Accept": "application/json"}
        if self._bearer_token:
            request_headers["Authorization"] = f"Bearer {self._bearer_token}"
        if json_payload is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update({str(key): str(value) for key, value in headers.items()})
        return request_headers

    @staticmethod
    def _request_body(json_payload: Mapping[str, Any] | None) -> str | None:
        if json_payload is None:
            return None
        return json.dumps(dict(json_payload), separators=(",", ":"))

    @staticmethod
    def state_token_from_response(response: ControlApiClientResponse) -> str:
        header_token = str(response.headers.get("X-State-Token", "")).strip()
        if header_token:
            return header_token.strip('"')
        payload = response.json()
        state = payload.get("state")
        if isinstance(state, dict):
            token = state.get("state_token")
            if isinstance(token, str):
                return token.strip().strip('"')
        return ""
