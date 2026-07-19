# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
from collections.abc import Mapping
from typing import Any

from venus_evcharger.control.events import ControlApiEventBus
from venus_evcharger.control.idempotency import ControlApiIdempotencyStore
from venus_evcharger.control.models import ControlCommand, ControlCommandSource, ControlResult
from venus_evcharger.control.rate_limit import ControlApiRateLimiter


class _FakeHandler:
    def __init__(
        self,
        path: str,
        *,
        body: bytes = b"{}",
        authorization: str = "",
        client_host: str = "127.0.0.1",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.headers = {
            "Content-Length": str(len(body)),
            "Authorization": authorization,
        }
        if headers:
            self.headers.update(headers)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_code: int | None = None
        self.response_headers: dict[str, str] = {}
        self.client_address = (client_host, 12345)

    def send_response(self, status: int) -> None:
        self.status_code = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers[key] = value

    def end_headers(self) -> None:
        return None

    def json_payload(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class ControlApiHttpServiceHarness:
    """Complete typed boundary used by focused HTTP component tests."""

    def __init__(self, **overrides: object) -> None:
        self._event_bus = ControlApiEventBus()
        self._idempotency_store = ControlApiIdempotencyStore()
        self._rate_limiter = ControlApiRateLimiter()
        for name, value in overrides.items():
            setattr(self, name, value)

    def control_command_from_payload(
        self,
        payload: dict[str, Any],
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        raise ValueError(f"Unsupported control command: {payload!r} from {source}")

    @staticmethod
    def handle_control_command(command: ControlCommand) -> ControlResult:
        return ControlResult.rejected_result(command, detail="unsupported in HTTP test harness")

    def event_bus(self) -> ControlApiEventBus:
        return self._event_bus

    def idempotency_store(self) -> ControlApiIdempotencyStore:
        return self._idempotency_store

    def rate_limiter(self) -> ControlApiRateLimiter:
        return self._rate_limiter

    @staticmethod
    def state_token() -> str:
        return ""

    @staticmethod
    def capabilities_payload() -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "transport": "http",
            "command_names": [],
            "command_sources": ["http"],
            "state_endpoints": [],
            "endpoints": [],
            "supported_phase_selections": [],
            "features": {},
            "topology": {},
        }

    @staticmethod
    def _state_payload(kind: str) -> dict[str, Any]:
        return {"ok": True, "api_version": "v1", "kind": kind, "state": {}}

    def automation_payload(self) -> dict[str, Any]:
        return self._state_payload("automation")

    def build_payload(self) -> dict[str, Any]:
        return self._state_payload("build")

    def config_effective_payload(self) -> dict[str, Any]:
        return self._state_payload("config-effective")

    def contracts_payload(self) -> dict[str, Any]:
        return self._state_payload("contracts")

    def dbus_diagnostics_payload(self) -> dict[str, Any]:
        return self._state_payload("dbus-diagnostics")

    def event_snapshot_payload(self) -> dict[str, Any]:
        return {"summary": self.summary_payload()}

    def health_payload(self) -> dict[str, Any]:
        return self._state_payload("health")

    def healthz_payload(self) -> dict[str, Any]:
        return self._state_payload("healthz")

    def operational_payload(self) -> dict[str, Any]:
        return self._state_payload("operational")

    def runtime_payload(self) -> dict[str, Any]:
        return self._state_payload("runtime")

    def summary_payload(self) -> dict[str, Any]:
        return self._state_payload("summary")

    def topology_payload(self) -> dict[str, Any]:
        return self._state_payload("topology")

    def update_payload(self) -> dict[str, Any]:
        return self._state_payload("update")

    def version_payload(self) -> dict[str, Any]:
        return self._state_payload("version")

    def victron_bias_recommendation_payload(self) -> dict[str, Any]:
        return self._state_payload("victron-bias-recommendation")

    def publish_command_event(
        self,
        command: ControlCommand | Mapping[str, Any] | None,
        result: ControlResult | Mapping[str, Any] | None,
        *,
        replayed: bool = False,
    ) -> None:
        del command, result, replayed

    @staticmethod
    def record_command_audit(
        *,
        command: ControlCommand | Mapping[str, Any] | None,
        result: ControlResult | Mapping[str, Any] | None,
        error: dict[str, Any] | None,
        replayed: bool,
        scope: str,
        client_host: str,
        status_code: int,
        transport: str = "http",
    ) -> dict[str, Any]:
        del command, result, error, replayed, scope, client_host, status_code, transport
        return {}


def control_api_http_service(**overrides: object) -> ControlApiHttpServiceHarness:
    return ControlApiHttpServiceHarness(**overrides)


__all__ = [name for name in globals() if not name.startswith("__")]
