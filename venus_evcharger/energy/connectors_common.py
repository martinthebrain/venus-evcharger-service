# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for external energy-source connectors."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, runtime_checkable

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    TemplateHttpBackendBase,
    json_path_value,
)
from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag

T = TypeVar("T")


def _empty_runtime_caches() -> dict[str, dict[str, object]]:
    return {}


@runtime_checkable
class EnergyConnectorOwnerPort(Protocol):
    """Optional service wrapper accepted at the connector facade."""

    service: object


@runtime_checkable
class RequestTimeoutDefaultPort(Protocol):
    """Runtime value used when a connector has no explicit timeout."""

    shelly_request_timeout_seconds: float


@runtime_checkable
class RequestTimeoutLimiterPort(Protocol):
    """Cooperative cycle-deadline limiter supplied by the scheduler."""

    def bounded_request_timeout_seconds(self, configured_seconds: float) -> float: ...


@dataclass(slots=True)
class EnergyConnectorRuntimeState:
    """Typed connector-owned caches attached to one scheduler runtime."""

    caches: dict[str, dict[str, object]] = field(default_factory=_empty_runtime_caches)


@runtime_checkable
class EnergyConnectorRuntimeStatePort(Protocol):
    """Runtime carrying the connector-owned cache state."""

    _energy_connector_runtime_state: EnergyConnectorRuntimeState


class EnergySourceHttpClient(TemplateHttpBackendBase):
    """Apply a helper-owned cycle deadline to every HTTP request."""

    def __init__(
        self,
        service: object,
        timeout_seconds: float,
        *,
        auth_settings: TemplateAuthSettings | None = None,
    ) -> None:
        super().__init__(
            service,
            timeout_seconds,
            auth_settings=auth_settings,
            max_response_bytes=262144,
        )

    def _perform_request(
        self,
        method: str,
        url: str,
        *,
        context: dict[str, str] | None = None,
        json_template: str | None = None,
    ) -> dict[str, object]:
        configured_timeout = self.timeout_seconds
        self.timeout_seconds = _bounded_request_timeout_seconds(self.service, configured_timeout)
        try:
            return super()._perform_request(
                method,
                url,
                context=context,
                json_template=json_template,
            )
        finally:
            self.timeout_seconds = configured_timeout


def _bounded_request_timeout_seconds(runtime: object, configured_seconds: float) -> float:
    configured = max(0.001, float(configured_seconds))
    if not isinstance(runtime, RequestTimeoutLimiterPort):
        return configured
    return max(
        0.001,
        min(configured, float(runtime.bounded_request_timeout_seconds(configured))),
    )


def _runtime_default_timeout_seconds(runtime: object, default_seconds: float) -> float:
    if not isinstance(runtime, RequestTimeoutDefaultPort):
        return float(default_seconds)
    return float(runtime.shelly_request_timeout_seconds or default_seconds)


def _runtime_owner(owner: object) -> object:
    return owner.service if isinstance(owner, EnergyConnectorOwnerPort) else owner


def _runtime_state(runtime: object) -> EnergyConnectorRuntimeState:
    if isinstance(runtime, EnergyConnectorRuntimeStatePort):
        return runtime._energy_connector_runtime_state
    state = EnergyConnectorRuntimeState()
    setattr(runtime, "_energy_connector_runtime_state", state)
    return state


def _runtime_cache_get(
    runtime: object,
    namespace: str,
    key: str,
    expected_type: type[T],
) -> T | None:
    state = _runtime_state(runtime)
    cache = state.caches.setdefault(namespace, {})
    value = cache.get(key)
    if isinstance(value, expected_type):
        return value
    cache.pop(key, None)
    return None


def _runtime_cache_put(
    runtime: object,
    namespace: str,
    key: str,
    value: object,
) -> None:
    state = _runtime_state(runtime)
    state.caches.setdefault(namespace, {})[key] = value


def _runtime_cache_pop(
    runtime: object,
    namespace: str,
    key: str,
) -> object | None:
    state = _runtime_state(runtime)
    cache = state.caches.get(namespace)
    if cache is None:
        return None
    return cache.pop(key, None)


def _normalized_connector_type(raw_value: object) -> str:
    return str(raw_value).strip().lower()


def _optional_path(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_float_path(payload: dict[str, object], path: str | None) -> float | None:
    if path is None:
        return None
    return finite_float_or_none(json_path_value(payload, path))


def _optional_text_path(payload: dict[str, object], path: str | None) -> str | None:
    if path is None:
        return None
    value = json_path_value(payload, path)
    text = "" if value is None else str(value).strip()
    return text or None


def _optional_bool_path(payload: dict[str, object], path: str | None) -> bool | None:
    if path is None:
        return None
    value = json_path_value(payload, path)
    normalized = _normalized_optional_bool_value(value)
    if normalized is not None:
        return normalized
    return bool(normalize_binary_flag(value))


def _normalized_optional_bool_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    return _normalized_optional_bool_text(value) if isinstance(value, str) else None


def _normalized_optional_bool_text(value: str) -> bool | None:
    return {
        "true": True,
        "yes": True,
        "on": True,
        "enabled": True,
        "false": False,
        "no": False,
        "off": False,
        "disabled": False,
    }.get(value.strip().lower())


def _optional_confidence_path(payload: dict[str, object], path: str | None) -> float | None:
    value = _optional_float_path(payload, path)
    if value is None:
        return None
    return min(1.0, max(0.0, float(value)))


def _sum_optional(values: Iterable[float | None]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return float(sum(numeric))


def _csv_filter(raw_value: object) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    raw = str(raw_value).strip()
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())
