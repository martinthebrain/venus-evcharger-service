# SPDX-License-Identifier: GPL-3.0-or-later
"""Request and RPC helpers for Shelly I/O support."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode

from requests.auth import HTTPDigestAuth

from venus_evcharger.backend.shelly_io_ports import ShellyRequestHost
from venus_evcharger.backend.shelly_io_types import (
    EncodedRpcScalar,
    JsonObject,
    ShellyHttpResponse,
    ShellyHttpSession,
    ShellyRpcScalar,
    _RequestAuthKwargs,
    _RequestKwargs,
    normalized_json_object,
)


class ShellyRequestClient:
    """Encapsulate low-level request and direct RPC helpers."""

    def __init__(self, service: ShellyRequestHost) -> None:
        self.service = service

    @staticmethod
    def _encoded_rpc_params(params: Mapping[str, ShellyRpcScalar]) -> dict[str, EncodedRpcScalar]:
        """Encode Shelly RPC query parameters, keeping booleans lowercase."""
        encoded: dict[str, EncodedRpcScalar] = {}
        for key, value in params.items():
            encoded[key] = str(value).lower() if isinstance(value, bool) else value
        return encoded

    def _request_auth_kwargs(self) -> _RequestAuthKwargs:
        """Return optional request auth kwargs for the configured Shelly auth mode."""
        svc = self.service
        if svc.use_digest_auth:
            return {"auth": HTTPDigestAuth(svc.username, svc.password)}
        if svc.username and svc.password:
            return {"auth": (svc.username, svc.password)}
        return {}

    def _request_kwargs(self, url: str) -> _RequestKwargs:
        """Return common request kwargs for main or worker HTTP sessions."""
        svc = self.service
        kwargs: _RequestKwargs = {
            "url": url,
            "timeout": float(getattr(svc, "shelly_request_timeout_seconds", 2.0)),
        }
        auth_kwargs = self._request_auth_kwargs()
        if "auth" in auth_kwargs:
            kwargs["auth"] = auth_kwargs["auth"]
        return kwargs

    def _rpc_url(self, method: str, params: Mapping[str, ShellyRpcScalar] | None) -> str:
        """Build a Shelly RPC URL including optional query parameters."""
        svc = self.service
        if not params:
            return f"http://{svc.host}/rpc/{method}"
        return f"http://{svc.host}/rpc/{method}?{urlencode(self._encoded_rpc_params(params))}"

    @staticmethod
    def _json_object(value: object) -> JsonObject:
        """Return JSON responses as a typed object mapping."""
        return normalized_json_object(value, error_message="Shelly response must be a JSON object")

    def request(self, url: str) -> JsonObject:
        """Perform a Shelly HTTP request through the main-session client."""
        response = self._get(self.service.session, url)
        response.raise_for_status()
        return self._json_object(response.json())

    def request_with_session(self, session: ShellyHttpSession, url: str) -> JsonObject:
        """Perform a Shelly HTTP request through a specific requests session."""
        response = self._get(session, url)
        response.raise_for_status()
        return self._json_object(response.json())

    def rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject:
        """Perform a Shelly RPC call with query encoding."""
        return self.request(self._rpc_url(method, params))

    def rpc_call_with_session(
        self,
        session: ShellyHttpSession,
        method: str,
        **params: ShellyRpcScalar,
    ) -> JsonObject:
        """Perform a Shelly RPC call through a specific requests session."""
        return self.request_with_session(session, self._rpc_url(method, params))

    def _get(self, session: ShellyHttpSession, url: str) -> ShellyHttpResponse:
        """Issue one GET while preserving requests-compatible optional auth."""
        kwargs = self._request_kwargs(url)
        if "auth" in kwargs:
            return session.get(url=url, timeout=kwargs["timeout"], auth=kwargs["auth"])
        return session.get(url=url, timeout=kwargs["timeout"])

    def fetch_pm_status_rpc(self) -> JsonObject:
        """Fetch Shelly component status for power data through the legacy direct RPC path."""
        svc = self.service
        return self.rpc_call(f"{svc.pm_component}.GetStatus", id=svc.pm_id)

    def set_relay_rpc(self, on: bool) -> JsonObject:
        """Switch the Shelly relay output through the legacy direct RPC path."""
        svc = self.service
        return self.rpc_call("Switch.Set", id=svc.pm_id, on=bool(on))

    def worker_fetch_pm_status_rpc(self) -> JsonObject:
        """Fetch Shelly power status from the background worker session."""
        svc = self.service
        return self.rpc_call_with_session(
            svc._worker_session,
            f"{svc.pm_component}.GetStatus",
            id=svc.pm_id,
        )


__all__ = ["ShellyRequestClient"]
