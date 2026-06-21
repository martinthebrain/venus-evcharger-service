# SPDX-License-Identifier: GPL-3.0-or-later
"""Request and RPC helpers for Shelly I/O support."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode

from requests.auth import HTTPDigestAuth

from venus_evcharger.backend.models import ChargerState
from venus_evcharger.backend.shelly_io_types import (
    EncodedRpcScalar,
    JsonObject,
    ShellyIoHost,
    ShellyRpcScalar,
    _EnableBackendLike,
    _RequestAuthKwargs,
    _RequestKwargs,
    _SessionLike,
)


class ShellyIoRequestsMixin:
    """Encapsulate low-level request and direct RPC helpers."""

    if TYPE_CHECKING:
        service: ShellyIoHost

        def _runtime_now(self) -> float: ...

        def _read_charger_state_best_effort(self, now: float | None = None) -> ChargerState | None: ...

        def _uses_split_backends(self) -> bool: ...

        def _read_split_pm_status(
            self,
            charger_state: ChargerState | None = None,
            *,
            now: float | None = None,
        ) -> JsonObject: ...

        def _split_enable_backend(self) -> _EnableBackendLike | None: ...

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
        return cast(JsonObject, value)

    def request(self, url: str) -> JsonObject:
        """Perform a Shelly HTTP request through the main-session client."""
        response = self.service.session.get(**self._request_kwargs(url))
        response.raise_for_status()
        return self._json_object(response.json())

    def request_with_session(self, session: _SessionLike, url: str) -> JsonObject:
        """Perform a Shelly HTTP request through a specific requests session."""
        response = session.get(**self._request_kwargs(url))
        response.raise_for_status()
        return self._json_object(response.json())

    def rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject:
        """Perform a Shelly RPC call with query encoding."""
        return self.service._request(self._rpc_url(method, params))

    def rpc_call_with_session(
        self,
        session: object,
        method: str,
        **params: ShellyRpcScalar,
    ) -> JsonObject:
        """Perform a Shelly RPC call through a specific requests session."""
        return self.service._request_with_session(session, self._rpc_url(method, params))

    def fetch_pm_status_rpc(self) -> JsonObject:
        """Fetch Shelly component status for power data through the legacy direct RPC path."""
        svc = self.service
        return svc.rpc_call(f"{svc.pm_component}.GetStatus", id=svc.pm_id)

    def fetch_pm_status(self) -> JsonObject:
        """Fetch PM status through the selected runtime backend seam."""
        now = self._runtime_now()
        charger_state = self._read_charger_state_best_effort(now=now)
        if self._uses_split_backends():
            return self._read_split_pm_status(charger_state, now=now)
        return self.fetch_pm_status_rpc()

    def set_relay_rpc(self, on: bool) -> JsonObject:
        """Switch the Shelly relay output through the legacy direct RPC path."""
        svc = self.service
        return svc.rpc_call("Switch.Set", id=svc.pm_id, on=bool(on))

    def set_relay(self, on: bool) -> JsonObject:
        """Switch relay state through the selected runtime backend seam."""
        backend = self._split_enable_backend()
        if backend is not None:
            backend.set_enabled(bool(on))
            return {"output": bool(on)}
        return self.set_relay_rpc(on)

    def worker_fetch_pm_status_rpc(self) -> JsonObject:
        """Fetch Shelly power status from the background worker session."""
        svc = self.service
        return svc._rpc_call_with_session(
            svc._worker_session,
            f"{svc.pm_component}.GetStatus",
            id=svc.pm_id,
        )

    def worker_fetch_pm_status(self) -> JsonObject:
        """Fetch PM status for the worker, using split backends when configured."""
        now = self._runtime_now()
        charger_state = self._read_charger_state_best_effort(now=now)
        if self._uses_split_backends():
            return self._read_split_pm_status(charger_state, now=now)
        return self.worker_fetch_pm_status_rpc()
