# SPDX-License-Identifier: GPL-3.0-or-later
"""Charger health, transport, and contactor heuristics for the update cycle.

This module centralizes safety-oriented runtime diagnostics: charger transport
health, retry visibility, contactor suspicion, and feedback mismatch state.
"""

from __future__ import annotations

from typing import Protocol, TypeGuard

from venus_evcharger.backend.models import ChargerState, SwitchState, switch_feedback_mismatch
from venus_evcharger.core.common import (
    _charger_transport_health_reason,
    _fresh_charger_retry_reason,
    _fresh_charger_transport_reason,
)

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.readback_resolver import FreshReadbacks
from venus_evcharger.update.relay_charger_readback import ChargerBackendAccess
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker

# Safety invariants for charger health:
# - Direct switch feedback and interlock faults override heuristic contactor guesses.
# - Transport retry and transport failure windows are surfaced before status text.
# - Contactor suspicion must persist beyond the configured delay before becoming health.
# - Repeated contactor suspicions can latch a lockout with an explicit source.
# - Lockout reasons are normalized to a small public diagnostic vocabulary.
# - Charger-side power/current can corroborate load when the Shelly reading is stale.
# - Text status hints influence presentation only; they do not override safety faults.
# - Runtime fault counters are sanitized before thresholds are evaluated.
# - Clearing feedback mismatches also clears transient contactor suspicion timers.
# - Backend enable state is preferred over relay state when fresh readback exists.


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


class ReadbackResolverPort(Protocol):
    def resolve(self, now: float | None = None) -> FreshReadbacks: ...


class ChargerHealthService(Protocol):
    @property
    def _readback_resolver(self) -> ReadbackResolverPort: ...


class ChargerHealthMonitor:
    """Combine charger transport, contactor heuristics, and status overrides."""

    def __init__(
        self,
        backends: ChargerBackendAccess,
        transport: ChargerTransportTracker,
        *,
        charging_tokens: frozenset[str],
        ready_tokens: frozenset[str],
        waiting_tokens: frozenset[str],
        finished_tokens: frozenset[str],
    ) -> None:
        self._backends = backends
        self._transport = transport
        self._charging_tokens = charging_tokens
        self._ready_tokens = ready_tokens
        self._waiting_tokens = waiting_tokens
        self._finished_tokens = finished_tokens

    @staticmethod
    def _contactor_heuristic_delay_seconds(svc: object) -> float:
        return max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 0.0)))

    @staticmethod
    def _contactor_lockout_threshold(svc: object) -> int:
        return max(0, int(getattr(svc, "auto_contactor_fault_latch_count", 3)))

    @staticmethod
    def _contactor_lockout_persistence_seconds(svc: object) -> float:
        return max(0.0, float(getattr(svc, "auto_contactor_fault_latch_seconds", 60.0)))

    @staticmethod
    def _contactor_power_threshold_w(svc: object) -> float:
        configured = finite_float_or_none(getattr(svc, "charging_threshold_watts", None))
        return 100.0 if configured is None else max(100.0, float(configured))

    @staticmethod
    def _contactor_current_threshold_a(svc: object) -> float:
        configured = finite_float_or_none(getattr(svc, "min_current", None))
        return 1.0 if configured is None else max(1.0, float(configured) / 4.0)

    def _pm_load_active(
        self,
        svc: ChargerHealthService,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
    ) -> bool:
        if not pm_confirmed:
            return False
        if power is not None and float(power) >= self._contactor_power_threshold_w(svc):
            return True
        return current is not None and float(current) >= self._contactor_current_threshold_a(svc)

    def _charger_load_active(self, svc: ChargerHealthService, now: float | None = None) -> bool:
        readback = svc._readback_resolver.resolve(now).charger
        return False if readback is None else self._charger_state_load_active(svc, readback.state)

    def _charger_state_load_active(self, svc: ChargerHealthService, state: ChargerState) -> bool:
        power = state.power_w
        if power is not None and float(power) >= self._contactor_power_threshold_w(svc):
            return True
        current = state.actual_current_amps
        return current is not None and float(current) >= self._contactor_current_threshold_a(svc)

    def _charger_requests_load(self, svc: ChargerHealthService, now: float | None = None) -> bool:
        readback = svc._readback_resolver.resolve(now).charger
        if readback is None:
            return False
        return self._charger_state_requests_load(svc, readback.state)

    def _charger_state_requests_load(self, svc: ChargerHealthService, state: ChargerState) -> bool:
        if self._charger_state_load_active(svc, state):
            return True
        tokens = self._backends.text_tokens(state.status_text)
        return bool(tokens & set(self._charging_tokens))

    def _observed_load_active(
        self,
        svc: ChargerHealthService,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        now: float | None = None,
    ) -> bool:
        if self._pm_load_active(svc, power, current, pm_confirmed):
            return True
        readback = svc._readback_resolver.resolve(now).charger
        return readback is not None and self._charger_state_load_active(svc, readback.state)

    def _heuristic_condition_age(
        self,
        svc: ChargerHealthService,
        attribute_name: str,
        condition_active: bool,
        now: float | None,
    ) -> float | None:
        current = self._transport.now(svc, now)
        if not condition_active:
            self._transport.set_runtime_attr(svc, attribute_name, None)
            return None
        started_at = finite_float_or_none(getattr(svc, attribute_name, None))
        if started_at is None:
            self._transport.set_runtime_attr(svc, attribute_name, current)
            return 0.0
        return float(max(0.0, current - float(started_at)))

    @staticmethod
    def _base_contactor_fault_reason(reason: object) -> str | None:
        if reason is None:
            return None
        normalized = str(reason).strip()
        if normalized in {"contactor-suspected-open", "contactor-suspected-welded"}:
            return normalized
        return None

    def _contactor_lockout_health_reason(self, base_reason: object) -> str | None:
        normalized = self._base_contactor_fault_reason(base_reason)
        if normalized == "contactor-suspected-open":
            return "contactor-lockout-open"
        if normalized == "contactor-suspected-welded":
            return "contactor-lockout-welded"
        return None

    def _contactor_fault_counts(self, svc: ChargerHealthService) -> dict[str, int]:
        counts = getattr(svc, "_contactor_fault_counts", None)
        if _is_object_dict(counts):
            normalized = self._normalized_contactor_fault_counts(counts)
            self._transport.set_runtime_attr(svc, "_contactor_fault_counts", normalized)
            return normalized
        empty_counts: dict[str, int] = {}
        self._transport.set_runtime_attr(svc, "_contactor_fault_counts", empty_counts)
        return empty_counts

    @staticmethod
    def _normalized_contactor_fault_counts(counts: dict[object, object]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for reason, count in counts.items():
            if reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
                continue
            if not isinstance(count, int) or isinstance(count, bool):
                continue
            normalized[str(reason)] = max(0, count)
        return normalized

    def _contactor_fault_count(self, svc: ChargerHealthService, reason: object) -> int:
        normalized = self._base_contactor_fault_reason(reason)
        if normalized is None:
            return 0
        return max(0, int(self._contactor_fault_counts(svc).get(normalized, 0)))

    def _clear_contactor_fault_active_state(self, svc: ChargerHealthService) -> None:
        self._transport.set_runtime_attr(svc, "_contactor_fault_active_reason", None)
        self._transport.set_runtime_attr(svc, "_contactor_fault_active_since", None)

    def _clear_contactor_lockout(self, svc: ChargerHealthService) -> None:
        self._transport.set_runtime_attr(svc, "_contactor_lockout_reason", "")
        self._transport.set_runtime_attr(svc, "_contactor_lockout_source", "")
        self._transport.set_runtime_attr(svc, "_contactor_lockout_at", None)

    def _clear_contactor_fault_tracking(self, svc: ChargerHealthService) -> None:
        self._transport.set_runtime_attr(svc, "_contactor_fault_counts", {})
        self._clear_contactor_fault_active_state(svc)
        self._clear_contactor_lockout(svc)
        self._transport.set_runtime_attr(svc, "_contactor_suspected_open_since", None)
        self._transport.set_runtime_attr(svc, "_contactor_suspected_welded_since", None)

    def _engage_contactor_lockout(
        self,
        svc: ChargerHealthService,
        base_reason: object,
        now: float | None,
        source: str,
    ) -> None:
        normalized = self._base_contactor_fault_reason(base_reason)
        if normalized is None:
            self._clear_contactor_lockout(svc)
            return
        current = self._transport.now(svc, now)
        self._transport.set_runtime_attr(svc, "_contactor_lockout_reason", normalized)
        self._transport.set_runtime_attr(svc, "_contactor_lockout_source", str(source).strip() or "count-threshold")
        self._transport.set_runtime_attr(svc, "_contactor_lockout_at", current)

    def _active_contactor_lockout_health(self, svc: ChargerHealthService) -> str | None:
        if not hasattr(svc, "_contactor_lockout_reason"):
            return None
        return self._contactor_lockout_health_reason(getattr(svc, "_contactor_lockout_reason"))

    def _remember_contactor_fault(self, svc: ChargerHealthService, reason: object, now: float | None) -> str | None:
        normalized = self._base_contactor_fault_reason(reason)
        if normalized is None:
            self._clear_contactor_fault_active_state(svc)
            return None
        current = self._transport.now(svc, now)
        active_since = self._activate_contactor_fault_reason(svc, normalized, current)
        current_count = self._contactor_fault_count(svc, normalized)
        if self._contactor_fault_exceeds_count_threshold(svc, current_count):
            self._engage_contactor_lockout(svc, normalized, current, "count-threshold")
            return self._active_contactor_lockout_health(svc)
        persistence_seconds = self._contactor_lockout_persistence_seconds(svc)
        if persistence_seconds > 0.0 and (current - active_since) >= persistence_seconds:
            self._engage_contactor_lockout(svc, normalized, current, "persistent")
            return self._active_contactor_lockout_health(svc)
        return normalized

    def _activate_contactor_fault_reason(self, svc: ChargerHealthService, normalized: str, current: float) -> float:
        active_reason = self._base_contactor_fault_reason(getattr(svc, "_contactor_fault_active_reason", None))
        active_since = finite_float_or_none(getattr(svc, "_contactor_fault_active_since", None))
        if active_reason == normalized and active_since is not None:
            return active_since
        counts = self._contactor_fault_counts(svc)
        counts[normalized] = max(0, int(counts.get(normalized, 0))) + 1
        self._transport.set_runtime_attr(svc, "_contactor_fault_active_reason", normalized)
        self._transport.set_runtime_attr(svc, "_contactor_fault_active_since", current)
        return current

    def _contactor_fault_exceeds_count_threshold(self, svc: ChargerHealthService, current_count: int) -> bool:
        threshold = self._contactor_lockout_threshold(svc)
        return bool(threshold > 0 and current_count >= threshold)

    def charger_health_override(self, svc: ChargerHealthService, now: float | None = None) -> str | None:
        transport_health = self._active_charger_transport_health(svc, now)
        if transport_health is not None:
            return transport_health
        readback = svc._readback_resolver.resolve(now).charger
        return None if readback is None else self._charger_state_health(readback.state)

    @staticmethod
    def _active_charger_transport_health(svc: ChargerHealthService, now: float | None) -> str | None:
        transport_reason = _fresh_charger_transport_reason(svc, now)
        if transport_reason is not None:
            return _charger_transport_health_reason(transport_reason)
        retry_reason = _fresh_charger_retry_reason(svc, now)
        return None if retry_reason is None else _charger_transport_health_reason(retry_reason)

    def _charger_state_health(self, state: ChargerState) -> str | None:
        fault_detected = self._backends.text_indicates_fault(state.fault_text) or self._backends.text_indicates_fault(
            state.status_text
        )
        return "charger-fault" if fault_detected else None

    def switch_feedback_health_override(
        self,
        svc: ChargerHealthService,
        desired_relay: bool,
        relay_on: bool,
        now: float | None = None,
        *,
        power: float | None = None,
        current: float | None = None,
        pm_confirmed: bool = False,
    ) -> str | None:
        safety_override = self._switch_feedback_safety_override(svc, desired_relay, relay_on, now)
        if safety_override is not None:
            return safety_override
        latched_lockout = self._active_contactor_lockout_health(svc)
        if latched_lockout is not None:
            return latched_lockout
        return self._switch_feedback_heuristic_override(svc, relay_on, power, current, pm_confirmed, now)

    def _switch_feedback_safety_override(
        self,
        svc: ChargerHealthService,
        desired_relay: bool,
        relay_on: bool,
        now: float | None,
    ) -> str | None:
        readback = svc._readback_resolver.resolve(now).switch
        if readback is None:
            return None
        override = self._switch_state_safety_override(readback.state, desired_relay, relay_on)
        if override is not None:
            self._clear_contactor_suspicions(svc)
        return override

    @staticmethod
    def _switch_state_safety_override(
        state: SwitchState,
        desired_relay: bool,
        relay_on: bool,
    ) -> str | None:
        if state.interlock_ok is False and any((desired_relay, relay_on)):
            return "contactor-interlock"
        if switch_feedback_mismatch(relay_on, state.feedback_closed):
            return "contactor-feedback-mismatch"
        return None

    def _switch_feedback_heuristic_override(
        self,
        svc: ChargerHealthService,
        relay_on: bool,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        now: float | None,
    ) -> str | None:
        suspected_open_age, suspected_welded_age = self._contactor_suspected_ages(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
        )
        delay_seconds = self._contactor_heuristic_delay_seconds(svc)
        if suspected_welded_age is not None and suspected_welded_age >= delay_seconds:
            return self._remember_contactor_fault(svc, "contactor-suspected-welded", now)
        if suspected_open_age is not None and suspected_open_age >= delay_seconds:
            return self._remember_contactor_fault(svc, "contactor-suspected-open", now)
        self._clear_contactor_fault_active_state(svc)
        return None

    def _contactor_suspected_ages(
        self,
        svc: ChargerHealthService,
        relay_on: bool,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        now: float | None,
    ) -> tuple[float | None, float | None]:
        readback = svc._readback_resolver.resolve(now).charger
        state = None if readback is None else readback.state
        observed_load = self._combined_load_active(svc, power, current, pm_confirmed, state)
        demand_active = self._charger_state_requests_load_if_present(svc, state)
        return (
            self._heuristic_condition_age(
                svc,
                "_contactor_suspected_open_since",
                self._suspected_open_condition(relay_on, demand_active, observed_load),
                now,
            ),
            self._heuristic_condition_age(
                svc,
                "_contactor_suspected_welded_since",
                self._suspected_welded_condition(relay_on, observed_load),
                now,
            ),
        )

    def _combined_load_active(
        self,
        svc: ChargerHealthService,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        state: ChargerState | None,
    ) -> bool:
        if self._pm_load_active(svc, power, current, pm_confirmed):
            return True
        return state is not None and self._charger_state_load_active(svc, state)

    def _charger_state_requests_load_if_present(self, svc: ChargerHealthService, state: ChargerState | None) -> bool:
        return state is not None and self._charger_state_requests_load(svc, state)

    @staticmethod
    def _suspected_open_condition(relay_on: bool, demand_active: bool, observed_load: bool) -> bool:
        return all((relay_on, demand_active, not observed_load))

    @staticmethod
    def _suspected_welded_condition(relay_on: bool, observed_load: bool) -> bool:
        return not relay_on and observed_load

    def _clear_contactor_suspicions(self, svc: ChargerHealthService) -> None:
        self._clear_contactor_fault_active_state(svc)
        self._transport.set_runtime_attr(svc, "_contactor_suspected_open_since", None)
        self._transport.set_runtime_attr(svc, "_contactor_suspected_welded_since", None)

    def _charger_status_override(
        self,
        svc: ChargerHealthService,
        auto_mode_active: bool,
        now: float | None = None,
    ) -> tuple[int, str] | None:
        readback = svc._readback_resolver.resolve(now).charger
        status_text = None if readback is None else readback.state.status_text
        tokens = self._backends.text_tokens(status_text)
        if not tokens:
            return None
        return self._charger_status_override_from_tokens(svc, tokens, auto_mode_active)

    def _charger_status_override_from_tokens(
        self,
        svc: ChargerHealthService,
        tokens: set[str],
        auto_mode_active: bool,
    ) -> tuple[int, str] | None:
        for hint_tokens, status_code, status_source in self._charger_status_token_rules(svc, auto_mode_active):
            if tokens & hint_tokens:
                return status_code, status_source
        return None

    def _charger_status_token_rules(
        self,
        svc: ChargerHealthService,
        auto_mode_active: bool,
    ) -> tuple[tuple[set[str], int, str], ...]:
        return (
            (set(self._finished_tokens), 3, "charger-status-finished"),
            (set(self._waiting_tokens), 4 if auto_mode_active else 6, "charger-status-waiting"),
            (set(self._charging_tokens), 2, "charger-status-charging"),
            (set(self._ready_tokens), int(getattr(svc, "idle_status", 1)), "charger-status-ready"),
        )

    def _effective_enabled_state(self, svc: ChargerHealthService, relay_on: bool, now: float | None = None) -> bool:
        readback = svc._readback_resolver.resolve(now).charger
        charger_enabled = None if readback is None else readback.state.enabled
        return bool(relay_on) if charger_enabled is None else bool(charger_enabled)

    def _enable_control_source_key(self, svc: ChargerHealthService) -> str:
        return "charger" if self._backends.enable_backend(svc) is not None else "shelly"

    def _enable_control_label(self, svc: ChargerHealthService) -> str:
        return "charger backend" if self._backends.enable_backend(svc) is not None else "Shelly relay"


__all__ = ["ChargerHealthMonitor", "ChargerHealthService", "ReadbackResolverPort"]
