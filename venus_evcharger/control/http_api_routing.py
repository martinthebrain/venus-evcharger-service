# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, Callable, Mapping, cast

from venus_evcharger.control.http_api_response import _LocalControlApiResponseMixin


class _LocalControlApiRoutingMixin(_LocalControlApiResponseMixin):
    if TYPE_CHECKING:
        _service: Any
        _STATE_GET_ENDPOINTS: frozenset[str]

        def capabilities_payload(self) -> dict[str, Any]: ...

        def health_payload(self) -> dict[str, Any]: ...

        def openapi_payload(self) -> dict[str, Any]: ...

        def _auth_error(
            self,
            handler: BaseHTTPRequestHandler,
            *,
            required_scope: str,
        ) -> tuple[HTTPStatus, str, str] | None: ...

        def _command_post_auth_error(
            self,
            handler: BaseHTTPRequestHandler,
            payload: dict[str, Any],
        ) -> tuple[HTTPStatus, str, str] | None: ...

        def _locality_error(self, handler: BaseHTTPRequestHandler) -> tuple[HTTPStatus, str, str] | None: ...

        def _optimistic_concurrency_error(
            self,
            handler: BaseHTTPRequestHandler,
        ) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]] | None: ...

        def _parsed_request_target(self, target: str) -> tuple[str, dict[str, list[str]]]: ...

        def _read_json_payload(self, handler: BaseHTTPRequestHandler) -> dict[str, Any] | None: ...

        def _state_token_headers(self) -> dict[str, str]: ...

        def _write_command_result(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None: ...

        def _write_event_stream(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None: ...

    def _state_payload(self, path: str) -> dict[str, Any]:
        payload_getter_names: dict[str, str] = {
            "/v1/state/automation": "_state_api_automation_payload",
            "/v1/state/build": "_state_api_build_payload",
            "/v1/state/config-effective": "_state_api_config_effective_payload",
            "/v1/state/contracts": "_state_api_contracts_payload",
            "/v1/state/dbus-diagnostics": "_state_api_dbus_diagnostics_payload",
            "/v1/state/health": "_state_api_health_payload",
            "/v1/state/healthz": "_state_api_healthz_payload",
            "/v1/state/operational": "_state_api_operational_payload",
            "/v1/state/runtime": "_state_api_runtime_payload",
            "/v1/state/summary": "_state_api_summary_payload",
            "/v1/state/topology": "_state_api_topology_payload",
            "/v1/state/update": "_state_api_update_payload",
            "/v1/state/version": "_state_api_version_payload",
            "/v1/state/victron-bias-recommendation": "_state_api_victron_bias_recommendation_payload",
        }
        getter = cast(Callable[[], Any], getattr(self._service, payload_getter_names[path]))
        return cast(dict[str, Any], getter())

    def _public_get_payload(self, path: str) -> dict[str, Any] | None:
        if path == "/v1/control/health":
            return self.health_payload()
        if path == "/v1/state/healthz":
            return self._state_payload(path)
        if path == "/v1/openapi.json":
            return self.openapi_payload()
        return None

    def _authorized_get_payload(self, path: str) -> dict[str, Any] | None:
        if path == "/v1/capabilities":
            return self.capabilities_payload()
        if path in self._STATE_GET_ENDPOINTS:
            return self._state_payload(path)
        return None

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        path, params = self._parsed_request_target(handler.path)
        error = self._locality_error(handler)
        if error is not None:
            self._write_error(handler, *error)
            return
        public_payload = self._public_get_payload(path)
        if public_payload is not None:
            self._write_json(handler, HTTPStatus.OK, public_payload, extra_headers=self._state_token_headers())
            return
        if path == "/v1/events":
            self._handle_events_get(handler, params)
            return
        authorized_payload = self._authorized_get_payload(path)
        if authorized_payload is not None:
            self._handle_authorized_get(handler, authorized_payload)
            return
        self._write_error(handler, HTTPStatus.NOT_FOUND, "not_found", "Not found.")

    def _handle_events_get(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        auth_error = self._auth_error(handler, required_scope="read")
        if auth_error is not None:
            self._write_error(handler, *auth_error)
            return
        self._write_event_stream(handler, params)

    def _handle_authorized_get(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        auth_error = self._auth_error(handler, required_scope="read")
        if auth_error is not None:
            self._write_error(handler, *auth_error)
            return
        self._write_json(handler, HTTPStatus.OK, payload, extra_headers=self._state_token_headers())

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        path, _params = self._parsed_request_target(handler.path)
        payload = self._validated_command_post_payload(handler, path)
        if payload is not None:
            self._write_command_result(handler, payload)

    def _validated_command_post_payload(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
    ) -> dict[str, Any] | None:
        if self._write_post_access_error(handler, path):
            return None
        payload = self._command_post_payload(handler)
        if payload is None:
            return None
        if self._write_command_post_auth_error(handler, payload):
            return None
        if self._write_optimistic_concurrency_error(handler):
            return None
        return payload

    def _command_post_payload(self, handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
        payload = self._read_json_payload(handler)
        if isinstance(payload, dict):
            return payload
        return None

    def _write_post_target_error(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        post_target_error = self._post_target_error(path)
        if post_target_error is None:
            return False
        self._write_error(handler, *post_target_error)
        return True

    def _write_post_access_error(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        return self._write_post_target_error(handler, path) or self._write_locality_error(handler)

    def _write_locality_error(self, handler: BaseHTTPRequestHandler) -> bool:
        access_error = self._locality_error(handler)
        if access_error is None:
            return False
        self._write_error(handler, *access_error)
        return True

    def _write_command_post_auth_error(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any],
    ) -> bool:
        auth_error = self._command_post_auth_error(handler, payload)
        if auth_error is None:
            return False
        self._write_error(handler, *auth_error)
        return True

    def _write_optimistic_concurrency_error(self, handler: BaseHTTPRequestHandler) -> bool:
        concurrency_error = self._optimistic_concurrency_error(handler)
        if concurrency_error is None:
            return False
        status, response_payload, headers = concurrency_error
        self._write_json(handler, status, response_payload, extra_headers=headers)
        return True

    @staticmethod
    def _post_target_error(path: str) -> tuple[HTTPStatus, str, str] | None:
        if path == "/v1/control/command":
            return None
        return HTTPStatus.NOT_FOUND, "not_found", "Not found."
