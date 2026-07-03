# SPDX-License-Identifier: GPL-3.0-or-later
import http.client
import json
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from venus_evcharger.control.client import ControlApiClientResponse, LocalControlApiClient

_EMPTY_HEADERS: Mapping[str, str] = MappingProxyType({})


class _FakeHttpResponse(http.client.HTTPResponse):
    def __init__(self, status: int, body: str, headers: list[tuple[str, str]] | None = None) -> None:
        self.status = status
        self.fp = None
        self._body = body.encode("utf-8")
        self._headers = headers or []

    def read(self, amt: int | None = None) -> bytes:
        return self._body

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)


@dataclass(frozen=True, slots=True)
class _GetCall:
    path: str
    query: Mapping[str, Any] | None
    headers: Mapping[str, str] | None


class _RecordingGetClient(LocalControlApiClient):
    def __init__(self, response: ControlApiClientResponse, *, bearer_token: str = "") -> None:
        super().__init__(base_url="http://127.0.0.1:8765", bearer_token=bearer_token)
        self._response = response
        self.get_calls: list[_GetCall] = []

    def get(
        self,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ControlApiClientResponse:
        self.get_calls.append(_GetCall(path=path, query=query, headers=headers))
        return self._response


class _RecordingHttpConnection(http.client.HTTPConnection):
    def __init__(self, response: http.client.HTTPResponse) -> None:
        super().__init__("localhost")
        self._response = response
        self.requests: list[tuple[str, str, str | bytes | None, dict[str, str]]] = []
        self.close_count = 0

    def request(
        self,
        method: str,
        url: str,
        body: str | bytes | None = None,
        headers: Mapping[str, str] = _EMPTY_HEADERS,
        *,
        encode_chunked: bool = False,
    ) -> None:
        self.requests.append((method, url, body, dict(headers)))

    def getresponse(self) -> http.client.HTTPResponse:
        return self._response

    def close(self) -> None:
        self.close_count += 1


class _ConnectionClient(LocalControlApiClient):
    def __init__(self, connection: _RecordingHttpConnection) -> None:
        super().__init__(base_url="http://127.0.0.1:8765", bearer_token="token")
        self.connection = connection

    def _connection(self) -> http.client.HTTPConnection:
        return self.connection


@dataclass(frozen=True, slots=True)
class _StateCall:
    state_name: str
    headers: Mapping[str, str] | None


@dataclass(frozen=True, slots=True)
class _CommandCall:
    payload: dict[str, Any]
    idempotency_key: str
    command_id: str
    if_match: str
    state_token: str
    headers: Mapping[str, str] | None


class _SafeCommandClient(LocalControlApiClient):
    def __init__(
        self,
        state_response: ControlApiClientResponse,
        command_response: ControlApiClientResponse | None = None,
    ) -> None:
        super().__init__(base_url="http://127.0.0.1:8765", bearer_token="token")
        self._state_response = state_response
        self._command_response = command_response or ControlApiClientResponse(status=200, headers={}, body='{"ok":true}')
        self.state_calls: list[_StateCall] = []
        self.command_calls: list[_CommandCall] = []

    def state(self, state_name: str, *, headers: Mapping[str, str] | None = None) -> ControlApiClientResponse:
        self.state_calls.append(_StateCall(state_name=state_name, headers=headers))
        return self._state_response

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
        self.command_calls.append(
            _CommandCall(
                payload=dict(payload),
                idempotency_key=idempotency_key,
                command_id=command_id,
                if_match=if_match,
                state_token=state_token,
                headers=headers,
            )
        )
        return self._command_response


class TestVenusEvchargerControlClient(unittest.TestCase):
    def test_json_and_ndjson_helpers_cover_success_and_failure_paths(self) -> None:
        response = ControlApiClientResponse(status=200, headers={}, body='{"ok":true}')
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(
            ControlApiClientResponse(status=200, headers={}, body='{"a":1}\n\n{"b":2}\n').ndjson(),
            [{"a": 1}, {"b": 2}],
        )

        with self.assertRaises(ValueError):
            ControlApiClientResponse(status=200, headers={}, body='["not-an-object"]').json()

        with self.assertRaises(ValueError):
            ControlApiClientResponse(status=200, headers={}, body='["not-an-object"]\n').ndjson()

    def test_request_target_and_headers_cover_query_and_auth_branches(self) -> None:
        client = LocalControlApiClient(base_url="http://127.0.0.1:8765", bearer_token="token")

        self.assertEqual(
            client._request_target("/v1/events", query={"kind": ["command", "state"], "once": 1}),
            "/v1/events?kind=command&kind=state&once=1",
        )
        self.assertEqual(
            client._request_headers({"X-Test": "1"}, json_payload={"ok": True}),
            {
                "Accept": "application/json",
                "Authorization": "Bearer token",
                "Content-Type": "application/json",
                "X-Test": "1",
            },
        )
        self.assertEqual(LocalControlApiClient._request_body({"ok": True}), '{"ok":true}')
        self.assertIsNone(LocalControlApiClient._request_body(None))

    def test_state_passthrough_and_headers_without_bearer_token(self) -> None:
        client = _RecordingGetClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))

        response = client.state("/v1/state/runtime")

        self.assertEqual(response.status, 200)
        self.assertEqual(client.get_calls, [_GetCall("/v1/state/runtime", query=None, headers=None)])
        self.assertEqual(client._request_headers(None, json_payload=None), {"Accept": "application/json"})

    def test_state_helper_accepts_victron_bias_recommendation_short_name(self) -> None:
        client = _RecordingGetClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))

        response = client.state("victron-bias-recommendation")

        self.assertEqual(response.status, 200)
        self.assertEqual(client.get_calls, [_GetCall("/v1/state/victron-bias-recommendation", query=None, headers=None)])

    def test_health_and_openapi_delegate_to_get(self) -> None:
        client = _RecordingGetClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))

        health_response = client.health()
        automation_response = client.automation()
        openapi_response = client.openapi()

        self.assertEqual(health_response.status, 200)
        self.assertEqual(automation_response.status, 200)
        self.assertEqual(openapi_response.status, 200)
        self.assertEqual(
            client.get_calls,
            [
                _GetCall("/v1/control/health", query=None, headers=None),
                _GetCall("/v1/state/automation", query=None, headers=None),
                _GetCall("/v1/openapi.json", query=None, headers=None),
            ],
        )

    def test_request_uses_connection_and_returns_normalized_response(self) -> None:
        fake_connection = _RecordingHttpConnection(
            _FakeHttpResponse(
                200,
                json.dumps({"ok": True}),
                headers=[("X-State-Token", "rev-1")],
            )
        )
        client = _ConnectionClient(fake_connection)

        response = client.command(
            {"name": "set_mode", "value": 1},
            idempotency_key="idem-1",
            command_id="cmd-1",
            if_match="rev-0",
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-State-Token"], "rev-1")
        self.assertEqual(
            fake_connection.requests,
            [
                (
                    "POST",
                    "/v1/control/command",
                    '{"name":"set_mode","value":1}',
                    {
                        "Accept": "application/json",
                        "Authorization": "Bearer token",
                        "Content-Type": "application/json",
                        "Idempotency-Key": "idem-1",
                        "X-Command-Id": "cmd-1",
                        "If-Match": "rev-0",
                    },
                )
            ],
        )
        self.assertEqual(fake_connection.close_count, 1)

    def test_connection_chooses_unix_socket_when_configured(self) -> None:
        client = LocalControlApiClient(unix_socket_path="/tmp/control.sock")

        connection = client._connection()

        self.assertEqual(getattr(connection, "_unix_socket_path"), "/tmp/control.sock")

    def test_safe_command_reads_state_token_before_write(self) -> None:
        client = _SafeCommandClient(
            ControlApiClientResponse(
                status=200,
                headers={},
                body='{"ok":true,"kind":"automation","state":{"state_token":"rev-7"}}',
            ),
            ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'),
        )

        response = client.safe_command(
            {"name": "set_mode", "value": 1},
            idempotency_key="idem-7",
            command_id="cmd-7",
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(client.state_calls, [_StateCall("automation", headers=None)])
        self.assertEqual(
            client.command_calls,
            [
                _CommandCall(
                    payload={"name": "set_mode", "value": 1},
                    idempotency_key="idem-7",
                    command_id="cmd-7",
                    if_match="rev-7",
                    state_token="",
                    headers=None,
                )
            ],
        )

    def test_safe_command_requires_state_token(self) -> None:
        client = _SafeCommandClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true,"state":{}}'))

        with self.assertRaises(ValueError):
            client.safe_command({"name": "set_mode", "value": 1})


if __name__ == "__main__":
    unittest.main()
