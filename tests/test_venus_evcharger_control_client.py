# SPDX-License-Identifier: GPL-3.0-or-later
import http.client
import json
import socket
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from unittest.mock import patch

from venus_evcharger.control.client import ControlApiClientResponse, LocalControlApiClient, _UnixSocketHttpConnection

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


class _FakeUnixSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.connected_path = ""

    def settimeout(self, timeout: float | object) -> None:
        self.timeout = float(timeout)

    def connect(self, path: str) -> None:
        self.connected_path = path


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


class _RecordingStateClient(LocalControlApiClient):
    def __init__(self) -> None:
        super().__init__(base_url="http://127.0.0.1:8765")
        self.state_calls: list[_StateCall] = []

    def state(self, state_name: str, *, headers: Mapping[str, str] | None = None) -> ControlApiClientResponse:
        self.state_calls.append(_StateCall(state_name=state_name, headers=headers))
        return ControlApiClientResponse(status=200, headers={}, body='{"ok":true}')


class TestVenusEvchargerControlClient(unittest.TestCase):
    def test_client_constructor_normalizes_connection_settings(self) -> None:
        client = LocalControlApiClient(
            base_url="https://example.test:9443///",
            unix_socket_path=" /tmp/control.sock ",
            bearer_token=" token ",
            timeout="2.5",
        )

        self.assertEqual(client._base_url, "https://example.test:9443")
        self.assertEqual(client._unix_socket_path, "/tmp/control.sock")
        self.assertEqual(client._bearer_token, "token")
        self.assertEqual(client._timeout, 2.5)

        marker_client = LocalControlApiClient(base_url="http://example.test/apiX")
        self.assertEqual(marker_client._base_url, "http://example.test/apiX")

    def test_default_constructor_targets_local_http_api(self) -> None:
        client = LocalControlApiClient()

        connection = client._connection()

        self.assertEqual(client._base_url, "http://127.0.0.1:8765")
        self.assertEqual(client._unix_socket_path, "")
        self.assertEqual(client._bearer_token, "")
        self.assertEqual(client._timeout, 5.0)
        self.assertEqual(connection.host, "127.0.0.1")
        self.assertEqual(connection.port, 8765)

    def test_unix_stream_connection_uses_configured_path_and_timeout(self) -> None:
        fake_socket = _FakeUnixSocket()
        connection = _UnixSocketHttpConnection("/tmp/control.sock", timeout=1.25)

        self.assertEqual(connection.host, "localhost")
        with patch("venus_evcharger.control.client.socket.socket", return_value=fake_socket) as socket_factory:
            connection.connect()

        socket_factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        self.assertIs(connection.sock, fake_socket)
        self.assertEqual(fake_socket.timeout, 1.25)
        self.assertEqual(fake_socket.connected_path, "/tmp/control.sock")

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

    def test_request_target_covers_relative_paths_empty_query_and_sequence_values(self) -> None:
        client = LocalControlApiClient(base_url="http://127.0.0.1:8765")

        self.assertEqual(client._request_target("v1/state/runtime", query=None), "/v1/state/runtime")
        self.assertEqual(client._request_target("/v1/state/runtime", query={}), "/v1/state/runtime")
        self.assertEqual(
            client._request_target(
                "/v1/events",
                query={"kind": ("command", "state"), "after": 3, "blank": ""},
            ),
            "/v1/events?kind=command&kind=state&after=3&blank=",
        )

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

    def test_state_helper_normalizes_names_and_forwards_headers(self) -> None:
        client = _RecordingGetClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))
        headers = {"X-State": "1"}

        response = client.state(" Runtime ", headers=headers)

        self.assertEqual(response.status, 200)
        self.assertEqual(client.get_calls, [_GetCall("/v1/state/runtime", query=None, headers=headers)])

    def test_capabilities_health_and_openapi_delegate_to_get(self) -> None:
        client = _RecordingGetClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))
        headers = {"X-Test": "1"}

        capabilities_response = client.capabilities(headers=headers)
        health_response = client.health(headers=headers)
        openapi_response = client.openapi(headers=headers)

        self.assertEqual(capabilities_response.status, 200)
        self.assertEqual(health_response.status, 200)
        self.assertEqual(openapi_response.status, 200)
        self.assertEqual(
            client.get_calls,
            [
                _GetCall("/v1/capabilities", query=None, headers=headers),
                _GetCall("/v1/control/health", query=None, headers=headers),
                _GetCall("/v1/openapi.json", query=None, headers=headers),
            ],
        )

    def test_automation_delegates_exact_state_name(self) -> None:
        client = _RecordingStateClient()
        headers = {"X-Test": "1"}

        response = client.automation(headers=headers)

        self.assertEqual(response.status, 200)
        self.assertEqual(client.state_calls, [_StateCall("automation", headers=headers)])

    def test_events_builds_full_query_contract(self) -> None:
        client = _RecordingGetClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))
        headers = {"X-Events": "1"}

        response = client.events(
            limit=7,
            after=3,
            resume=5,
            timeout=2.5,
            heartbeat=0.75,
            kinds=(" command ", "", "state"),
            once=True,
            headers=headers,
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            client.get_calls,
            [
                _GetCall(
                    "/v1/events",
                    query={
                        "limit": 7,
                        "timeout": 2.5,
                        "heartbeat": 0.75,
                        "once": 1,
                        "after": 3,
                        "resume": 5,
                        "kind": "command,state",
                    },
                    headers=headers,
                )
            ],
        )

    def test_events_default_query_contract(self) -> None:
        client = _RecordingGetClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))

        response = client.events()

        self.assertEqual(response.status, 200)
        self.assertEqual(
            client.get_calls,
            [
                _GetCall(
                    "/v1/events",
                    query={
                        "limit": 20,
                        "timeout": 5.0,
                        "heartbeat": 1.0,
                        "once": 0,
                    },
                    headers=None,
                )
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
        self.assertEqual(response.body, '{"ok": true}')
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

    def test_command_without_optional_ids_does_not_emit_conditional_headers(self) -> None:
        fake_connection = _RecordingHttpConnection(_FakeHttpResponse(200, '{"ok":true}'))
        client = _ConnectionClient(fake_connection)

        response = client.command({"name": "set_mode", "value": 0})

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, '{"ok":true}')
        self.assertEqual(
            fake_connection.requests,
            [
                (
                    "POST",
                    "/v1/control/command",
                    '{"name":"set_mode","value":0}',
                    {
                        "Accept": "application/json",
                        "Authorization": "Bearer token",
                        "Content-Type": "application/json",
                    },
                )
            ],
        )

    def test_command_forwards_state_token_header(self) -> None:
        fake_connection = _RecordingHttpConnection(_FakeHttpResponse(200, '{"ok":true}'))
        client = _ConnectionClient(fake_connection)

        response = client.command({"name": "set_mode", "value": 1}, state_token="rev-state")

        self.assertEqual(response.status, 200)
        self.assertEqual(fake_connection.requests[0][3]["X-State-Token"], "rev-state")

    def test_command_forwards_custom_headers(self) -> None:
        fake_connection = _RecordingHttpConnection(_FakeHttpResponse(200, '{"ok":true}'))
        client = _ConnectionClient(fake_connection)

        response = client.command({"name": "set_mode", "value": 1}, headers={"X-Trace": "1"})

        self.assertEqual(response.status, 200)
        self.assertEqual(fake_connection.requests[0][3]["X-Trace"], "1")

    def test_get_uses_connection_with_headers_and_query(self) -> None:
        fake_connection = _RecordingHttpConnection(
            _FakeHttpResponse(204, "", headers=[("X-Test", "ok")])
        )
        client = _ConnectionClient(fake_connection)

        response = client.get(
            "v1/events",
            query={"kind": ["command", "state"], "once": 1},
            headers={"X-Caller": "test"},
        )

        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers, {"X-Test": "ok"})
        self.assertEqual(
            fake_connection.requests,
            [
                (
                    "GET",
                    "/v1/events?kind=command&kind=state&once=1",
                    None,
                    {
                        "Accept": "application/json",
                        "Authorization": "Bearer token",
                        "X-Caller": "test",
                    },
                )
            ],
        )
        self.assertEqual(fake_connection.close_count, 1)

    def test_connection_chooses_unix_stream_when_configured(self) -> None:
        client = LocalControlApiClient(unix_socket_path="/tmp/control.sock")

        connection = client._connection()

        self.assertEqual(getattr(connection, "_unix_socket_path"), "/tmp/control.sock")
        self.assertEqual(connection.timeout, 5.0)

    def test_connection_uses_base_url_scheme_host_and_port(self) -> None:
        https_client = LocalControlApiClient(base_url="https://api.example.test")
        http_client = LocalControlApiClient(base_url="http://api.example.test")
        fallback_host_client = LocalControlApiClient(base_url="")
        explicit_port_client = LocalControlApiClient(base_url="http://api.example.test:8080")

        https_connection = https_client._connection()
        http_connection = http_client._connection()
        fallback_host_connection = fallback_host_client._connection()
        explicit_connection = explicit_port_client._connection()

        self.assertEqual(https_connection.host, "api.example.test")
        self.assertEqual(https_connection.port, 443)
        self.assertEqual(https_connection.timeout, 5.0)
        self.assertEqual(http_connection.host, "api.example.test")
        self.assertEqual(http_connection.port, 80)
        self.assertEqual(fallback_host_connection.host, "127.0.0.1")
        self.assertEqual(fallback_host_connection.port, 80)
        self.assertEqual(explicit_connection.host, "api.example.test")
        self.assertEqual(explicit_connection.port, 8080)

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

    def test_safe_command_forwards_custom_state_endpoint_headers_and_header_token(self) -> None:
        headers = {"X-Trace": "1"}
        client = _SafeCommandClient(
            ControlApiClientResponse(
                status=200,
                headers={"X-State-Token": '"rev-header"'},
                body='{"ok":true,"kind":"runtime","state":{"state_token":"rev-body"}}',
            ),
            ControlApiClientResponse(status=202, headers={}, body='{"ok":true}'),
        )

        response = client.safe_command(
            {"name": "set_mode", "value": 2},
            state_endpoint="/v1/state/runtime",
            idempotency_key="idem-custom",
            command_id="cmd-custom",
            headers=headers,
        )

        self.assertEqual(response.status, 202)
        self.assertEqual(client.state_calls, [_StateCall("/v1/state/runtime", headers=headers)])
        self.assertEqual(
            client.command_calls,
            [
                _CommandCall(
                    payload={"name": "set_mode", "value": 2},
                    idempotency_key="idem-custom",
                    command_id="cmd-custom",
                    if_match="rev-header",
                    state_token="",
                    headers=headers,
                )
            ],
        )

    def test_safe_command_requires_state_token(self) -> None:
        client = _SafeCommandClient(ControlApiClientResponse(status=200, headers={}, body='{"ok":true,"state":{}}'))

        with self.assertRaisesRegex(ValueError, "State endpoint 'automation' did not return a state token"):
            client.safe_command({"name": "set_mode", "value": 1})

    def test_safe_command_default_headers_remain_empty_when_not_supplied(self) -> None:
        client = _SafeCommandClient(
            ControlApiClientResponse(
                status=200,
                headers={},
                body='{"ok":true,"state":{"state_token":"rev-default"}}',
            )
        )

        response = client.safe_command({"name": "set_mode", "value": 0})

        self.assertEqual(response.status, 200)
        self.assertEqual(
            client.command_calls,
            [
                _CommandCall(
                    payload={"name": "set_mode", "value": 0},
                    idempotency_key="",
                    command_id="",
                    if_match="rev-default",
                    state_token="",
                    headers=None,
                )
            ],
        )

    def test_state_token_from_response_contracts(self) -> None:
        self.assertEqual(
            LocalControlApiClient.state_token_from_response(
                ControlApiClientResponse(
                    status=200,
                    headers={"X-State-Token": ' "Xrev-headerX" '},
                    body='{"state":{"state_token":"rev-body"}}',
                )
            ),
            "Xrev-headerX",
        )
        self.assertEqual(
            LocalControlApiClient.state_token_from_response(
                ControlApiClientResponse(
                    status=200,
                    headers={},
                    body='{"state":{"state_token":"  \\"Xrev-bodyX\\"  "}}',
                )
            ),
            "Xrev-bodyX",
        )
        self.assertEqual(
            LocalControlApiClient.state_token_from_response(
                ControlApiClientResponse(status=200, headers={}, body='{"state":{"state_token":7}}')
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
