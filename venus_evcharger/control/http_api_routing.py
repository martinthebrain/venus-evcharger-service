# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit request routing for Control API HTTP."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

from venus_evcharger.control.http_api_auth import ControlApiHttpAuthenticator
from venus_evcharger.control.http_api_command_contracts import ControlApiStatePort
from venus_evcharger.control.http_api_commands import ControlApiHttpCommandEndpoint
from venus_evcharger.control.http_api_events import ControlApiHttpEventEndpoint
from venus_evcharger.control.http_api_response import ControlApiHttpResponder
from venus_evcharger.core.contracts_control_surface import CONTROL_API_STATE_ENDPOINTS


PayloadFactory = Callable[[], dict[str, Any]]


class ControlApiHttpStateReader:
    """Resolve stable state endpoint names without dynamic attribute lookup."""

    def __init__(self, service: ControlApiStatePort) -> None:
        self._getters: dict[str, PayloadFactory] = {
            "/v1/state/automation": service.automation_payload,
            "/v1/state/build": service.build_payload,
            "/v1/state/config-effective": service.config_effective_payload,
            "/v1/state/contracts": service.contracts_payload,
            "/v1/state/dbus-diagnostics": service.dbus_diagnostics_payload,
            "/v1/state/health": service.health_payload,
            "/v1/state/healthz": service.healthz_payload,
            "/v1/state/operational": service.operational_payload,
            "/v1/state/runtime": service.runtime_payload,
            "/v1/state/summary": service.summary_payload,
            "/v1/state/topology": service.topology_payload,
            "/v1/state/update": service.update_payload,
            "/v1/state/version": service.version_payload,
            "/v1/state/victron-bias-recommendation": service.victron_bias_recommendation_payload,
        }

    def payload(self, path: str) -> dict[str, Any]:
        payload = self._getters[path]()
        if not isinstance(payload, dict):
            raise TypeError(f"State payload getter for {path} must return dict, got {type(payload).__name__}")
        return {str(key): value for key, value in payload.items()}


class ControlApiHttpRouter:
    """Route HTTP requests across explicit Control API components."""

    STATE_GET_ENDPOINTS = CONTROL_API_STATE_ENDPOINTS

    def __init__(
        self,
        *,
        state_reader: ControlApiHttpStateReader,
        health_payload: PayloadFactory,
        capabilities_payload: PayloadFactory,
        openapi_payload: PayloadFactory,
        responder: ControlApiHttpResponder,
        authenticator: ControlApiHttpAuthenticator,
        commands: ControlApiHttpCommandEndpoint,
        events: ControlApiHttpEventEndpoint,
    ) -> None:
        self._state_reader = state_reader
        self._health_payload = health_payload
        self._capabilities_payload = capabilities_payload
        self._openapi_payload = openapi_payload
        self._responder = responder
        self._authenticator = authenticator
        self._commands = commands
        self._events = events

    def handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        path, params = self._authenticator.parse_request_target(handler.path)
        locality_error = self._authenticator.locality_error(handler)
        if locality_error is not None:
            self._responder.write_error(handler, *locality_error)
            return
        public_payload = self.public_get_payload(path)
        if public_payload is not None:
            self._responder.write_json(
                handler,
                HTTPStatus.OK,
                public_payload,
                extra_headers=self._authenticator.state_token_headers,
            )
            return
        if path == "/v1/events":
            self._handle_events_get(handler, params)
            return
        authorized_payload = self.authorized_get_payload(path)
        if authorized_payload is not None:
            self._handle_authorized_get(handler, authorized_payload)
            return
        self._responder.write_error(handler, HTTPStatus.NOT_FOUND, "not_found", "Not found.")

    def handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        path, _params = self._authenticator.parse_request_target(handler.path)
        if self._write_post_access_error(handler, path):
            return
        payload = self._commands.read_json_payload(handler)
        if payload is None or self._write_command_guard_error(handler, payload):
            return
        self._commands.write_command_result(handler, payload)

    def _write_post_access_error(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        target_error = self.post_target_error(path)
        if target_error is not None:
            self._responder.write_error(handler, *target_error)
            return True
        locality_error = self._authenticator.locality_error(handler)
        if locality_error is not None:
            self._responder.write_error(handler, *locality_error)
            return True
        auth_error = self._authenticator.command_transport_auth_error(handler)
        if auth_error is not None:
            self._responder.write_error(handler, *auth_error)
            return True
        return False

    def _write_command_guard_error(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> bool:
        auth_error = self._authenticator.command_auth_error(handler, payload)
        if auth_error is not None:
            self._responder.write_error(handler, *auth_error)
            return True
        concurrency_error = self._authenticator.concurrency_error(handler)
        if concurrency_error is not None:
            status, response_payload, headers = concurrency_error
            self._responder.write_json(handler, status, response_payload, extra_headers=headers)
            return True
        return False

    def public_get_payload(self, path: str) -> dict[str, Any] | None:
        if path == "/v1/control/health":
            return self._health_payload()
        if path == "/v1/state/healthz":
            return self._state_reader.payload(path)
        if path == "/v1/openapi.json":
            return self._openapi_payload()
        return None

    def authorized_get_payload(self, path: str) -> dict[str, Any] | None:
        if path == "/v1/capabilities":
            return self._capabilities_payload()
        if path in self.STATE_GET_ENDPOINTS:
            return self._state_reader.payload(path)
        return None

    def _handle_events_get(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        auth_error = self._authenticator.auth_error(handler, required_scope="read")
        if auth_error is not None:
            self._responder.write_error(handler, *auth_error)
            return
        self._events.write_stream(handler, params)

    def _handle_authorized_get(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        auth_error = self._authenticator.auth_error(handler, required_scope="read")
        if auth_error is not None:
            self._responder.write_error(handler, *auth_error)
            return
        self._responder.write_json(
            handler,
            HTTPStatus.OK,
            payload,
            extra_headers=self._authenticator.state_token_headers,
        )

    @staticmethod
    def post_target_error(path: str) -> tuple[HTTPStatus, str, str] | None:
        if path == "/v1/control/command":
            return None
        return HTTPStatus.NOT_FOUND, "not_found", "Not found."
