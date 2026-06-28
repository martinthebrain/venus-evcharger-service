# SPDX-License-Identifier: GPL-3.0-or-later
"""Charger health, transport, and contactor heuristics for the update cycle.

This module centralizes safety-oriented runtime diagnostics: charger transport
health, retry visibility, contactor suspicion, and feedback mismatch state.
"""

from __future__ import annotations

from typing import Any

from venus_evcharger.backend.models import switch_feedback_mismatch
from venus_evcharger.core.common import (
    _charger_transport_health_reason,
    _fresh_charger_retry_reason,
    _fresh_charger_transport_reason,
)

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_charger_contracts import _RelayChargerHealthContractsMixin
from venus_evcharger.update.relay_charger_transport import _RelayChargerTransportMixin


class _RelayChargerHealthMixin(_RelayChargerTransportMixin, _RelayChargerHealthContractsMixin):
    """Combine charger transport, contactor heuristics, and status overrides."""

    @classmethod
    def _pm_load_active(
        cls,
        svc: Any,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
    ) -> bool:
        if not pm_confirmed:
            return False
        if power is not None and float(power) >= cls._contactor_power_threshold_w(svc):
            return True
        return current is not None and float(current) >= cls._contactor_current_threshold_a(svc)

    @classmethod
    def _charger_load_active(cls, svc: Any, now: float | None = None) -> bool:
        power = cls._fresh_charger_power_readback(svc, now)
        if power is not None and float(power) >= cls._contactor_power_threshold_w(svc):
            return True
        current = cls._fresh_charger_actual_current_readback(svc, now)
        return current is not None and float(current) >= cls._contactor_current_threshold_a(svc)

    @classmethod
    def _charger_requests_load(cls, svc: Any, now: float | None = None) -> bool:
        if cls._charger_load_active(svc, now):
            return True
        tokens = cls._charger_text_tokens(cls._fresh_charger_text_readback(svc, "_last_charger_state_status", now))
        return bool(tokens & set(cls.CHARGER_STATUS_CHARGING_HINT_TOKENS))

    @classmethod
    def _observed_load_active(
        cls,
        svc: Any,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        now: float | None = None,
    ) -> bool:
        if cls._pm_load_active(svc, power, current, pm_confirmed):
            return True
        return cls._charger_load_active(svc, now)

    @classmethod
    def _heuristic_condition_age(
        cls,
        svc: Any,
        attribute_name: str,
        condition_active: bool,
        now: float | None,
    ) -> float | None:
        current = cls._charger_readback_now(svc, now)
        if not condition_active:
            cls._set_runtime_attr(svc, attribute_name, None)
            return None
        started_at = finite_float_or_none(getattr(svc, attribute_name, None))
        if started_at is None:
            cls._set_runtime_attr(svc, attribute_name, current)
            return 0.0
        return float(max(0.0, current - float(started_at)))

    @staticmethod
    def _base_contactor_fault_reason(reason: object) -> str | None:
        normalized = str(reason).strip() if reason is not None else ""
        if normalized in {"contactor-suspected-open", "contactor-suspected-welded"}:
            return normalized
        return None

    @classmethod
    def _contactor_lockout_health_reason(cls, base_reason: object) -> str | None:
        normalized = cls._base_contactor_fault_reason(base_reason)
        if normalized == "contactor-suspected-open":
            return "contactor-lockout-open"
        if normalized == "contactor-suspected-welded":
            return "contactor-lockout-welded"
        return None

    @staticmethod
    def _contactor_fault_counts(svc: Any) -> dict[str, int]:
        counts = getattr(svc, "_contactor_fault_counts", None)
        if isinstance(counts, dict):
            normalized = _RelayChargerHealthMixin._normalized_contactor_fault_counts(counts)
            _RelayChargerHealthMixin._set_runtime_attr(svc, "_contactor_fault_counts", normalized)
            return normalized
        empty_counts: dict[str, int] = {}
        _RelayChargerHealthMixin._set_runtime_attr(svc, "_contactor_fault_counts", empty_counts)
        return empty_counts

    @staticmethod
    def _normalized_contactor_fault_counts(counts: dict[Any, Any]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for reason, count in counts.items():
            if reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
                continue
            if not isinstance(count, int) or isinstance(count, bool):
                continue
            normalized[str(reason)] = max(0, count)
        return normalized

    @classmethod
    def _contactor_fault_count(cls, svc: Any, reason: object) -> int:
        normalized = cls._base_contactor_fault_reason(reason)
        if normalized is None:
            return 0
        return max(0, int(cls._contactor_fault_counts(svc).get(normalized, 0)))

    @classmethod
    def _clear_contactor_fault_active_state(cls, svc: Any) -> None:
        cls._set_runtime_attr(svc, "_contactor_fault_active_reason", None)
        cls._set_runtime_attr(svc, "_contactor_fault_active_since", None)

    @classmethod
    def _clear_contactor_lockout(cls, svc: Any) -> None:
        cls._set_runtime_attr(svc, "_contactor_lockout_reason", "")
        cls._set_runtime_attr(svc, "_contactor_lockout_source", "")
        cls._set_runtime_attr(svc, "_contactor_lockout_at", None)

    @classmethod
    def _clear_contactor_fault_tracking(cls, svc: Any) -> None:
        cls._set_runtime_attr(svc, "_contactor_fault_counts", {})
        cls._clear_contactor_fault_active_state(svc)
        cls._clear_contactor_lockout(svc)
        cls._set_runtime_attr(svc, "_contactor_suspected_open_since", None)
        cls._set_runtime_attr(svc, "_contactor_suspected_welded_since", None)

    @classmethod
    def _engage_contactor_lockout(
        cls,
        svc: Any,
        base_reason: object,
        now: float | None,
        source: str,
    ) -> None:
        normalized = cls._base_contactor_fault_reason(base_reason)
        if normalized is None:
            cls._clear_contactor_lockout(svc)
            return
        current = cls._charger_readback_now(svc, now)
        cls._set_runtime_attr(svc, "_contactor_lockout_reason", normalized)
        cls._set_runtime_attr(svc, "_contactor_lockout_source", str(source).strip() or "count-threshold")
        cls._set_runtime_attr(svc, "_contactor_lockout_at", current)

    @classmethod
    def _active_contactor_lockout_health(cls, svc: Any) -> str | None:
        return cls._contactor_lockout_health_reason(getattr(svc, "_contactor_lockout_reason", ""))

    @classmethod
    def _remember_contactor_fault(cls, svc: Any, reason: object, now: float | None) -> str | None:
        normalized = cls._base_contactor_fault_reason(reason)
        if normalized is None:
            cls._clear_contactor_fault_active_state(svc)
            return None
        current = cls._charger_readback_now(svc, now)
        active_since = cls._activate_contactor_fault_reason(svc, normalized, current)
        current_count = cls._contactor_fault_count(svc, normalized)
        if cls._contactor_fault_exceeds_count_threshold(svc, current_count):
            cls._engage_contactor_lockout(svc, normalized, current, "count-threshold")
            return cls._active_contactor_lockout_health(svc)
        persistence_seconds = cls._contactor_lockout_persistence_seconds(svc)
        if persistence_seconds > 0.0 and (current - active_since) >= persistence_seconds:
            cls._engage_contactor_lockout(svc, normalized, current, "persistent")
            return cls._active_contactor_lockout_health(svc)
        return normalized

    @classmethod
    def _activate_contactor_fault_reason(cls, svc: Any, normalized: str, current: float) -> float:
        active_reason = cls._base_contactor_fault_reason(getattr(svc, "_contactor_fault_active_reason", None))
        active_since = finite_float_or_none(getattr(svc, "_contactor_fault_active_since", None))
        if active_reason == normalized and active_since is not None:
            return active_since
        counts = cls._contactor_fault_counts(svc)
        counts[normalized] = max(0, int(counts.get(normalized, 0))) + 1
        cls._set_runtime_attr(svc, "_contactor_fault_active_reason", normalized)
        cls._set_runtime_attr(svc, "_contactor_fault_active_since", current)
        return current

    @classmethod
    def _contactor_fault_exceeds_count_threshold(cls, svc: Any, current_count: int) -> bool:
        threshold = cls._contactor_lockout_threshold(svc)
        return bool(threshold > 0 and current_count >= threshold)

    @classmethod
    def charger_health_override(cls, svc: Any, now: float | None = None) -> str | None:
        transport_reason = _fresh_charger_transport_reason(svc, now)
        if transport_reason is not None:
            return _charger_transport_health_reason(transport_reason)
        retry_reason = _fresh_charger_retry_reason(svc, now)
        if retry_reason is not None:
            return _charger_transport_health_reason(retry_reason)
        if cls._charger_text_indicates_fault(cls._fresh_charger_text_readback(svc, "_last_charger_state_fault", now)):
            return "charger-fault"
        if cls._charger_text_indicates_fault(cls._fresh_charger_text_readback(svc, "_last_charger_state_status", now)):
            return "charger-fault"
        return None

    @classmethod
    def switch_feedback_health_override(
        cls,
        svc: Any,
        desired_relay: bool,
        relay_on: bool,
        now: float | None = None,
        *,
        power: float | None = None,
        current: float | None = None,
        pm_confirmed: bool = False,
    ) -> str | None:
        safety_override = cls._switch_feedback_safety_override(svc, desired_relay, relay_on, now)
        if safety_override is not None:
            return safety_override
        latched_lockout = cls._active_contactor_lockout_health(svc)
        if latched_lockout is not None:
            return latched_lockout
        return cls._switch_feedback_heuristic_override(svc, relay_on, power, current, pm_confirmed, now)

    @classmethod
    def _switch_feedback_safety_override(
        cls,
        svc: Any,
        desired_relay: bool,
        relay_on: bool,
        now: float | None,
    ) -> str | None:
        interlock_ok = cls._fresh_switch_interlock_ok(svc, now)
        if interlock_ok is False and (bool(desired_relay) or bool(relay_on)):
            cls._clear_contactor_suspicions(svc)
            return "contactor-interlock"
        feedback_closed = cls._fresh_switch_feedback_closed(svc, now)
        if switch_feedback_mismatch(relay_on, feedback_closed):
            cls._clear_contactor_suspicions(svc)
            return "contactor-feedback-mismatch"
        return None

    @classmethod
    def _switch_feedback_heuristic_override(
        cls,
        svc: Any,
        relay_on: bool,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        now: float | None,
    ) -> str | None:
        suspected_open_age, suspected_welded_age = cls._contactor_suspected_ages(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
        )
        delay_seconds = cls._contactor_heuristic_delay_seconds(svc)
        if suspected_welded_age is not None and suspected_welded_age >= delay_seconds:
            return cls._remember_contactor_fault(svc, "contactor-suspected-welded", now)
        if suspected_open_age is not None and suspected_open_age >= delay_seconds:
            return cls._remember_contactor_fault(svc, "contactor-suspected-open", now)
        cls._clear_contactor_fault_active_state(svc)
        return None

    @classmethod
    def _contactor_suspected_ages(
        cls,
        svc: Any,
        relay_on: bool,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        now: float | None,
    ) -> tuple[float | None, float | None]:
        observed_load = cls._observed_load_active(svc, power, current, pm_confirmed, now)
        demand_active = cls._charger_requests_load(svc, now)
        return (
            cls._heuristic_condition_age(
                svc,
                "_contactor_suspected_open_since",
                bool(relay_on) and demand_active and not observed_load,
                now,
            ),
            cls._heuristic_condition_age(
                svc,
                "_contactor_suspected_welded_since",
                not bool(relay_on) and observed_load,
                now,
            ),
        )

    @classmethod
    def _clear_contactor_suspicions(cls, svc: Any) -> None:
        cls._clear_contactor_fault_active_state(svc)
        cls._set_runtime_attr(svc, "_contactor_suspected_open_since", None)
        cls._set_runtime_attr(svc, "_contactor_suspected_welded_since", None)

    @classmethod
    def _charger_status_override(
        cls,
        svc: Any,
        auto_mode_active: bool,
        now: float | None = None,
    ) -> tuple[int, str] | None:
        status_text = cls._fresh_charger_text_readback(svc, "_last_charger_state_status", now)
        tokens = cls._charger_text_tokens(status_text)
        if not tokens:
            return None
        return cls._charger_status_override_from_tokens(svc, tokens, auto_mode_active)

    @classmethod
    def _charger_status_override_from_tokens(
        cls,
        svc: Any,
        tokens: set[str],
        auto_mode_active: bool,
    ) -> tuple[int, str] | None:
        for hint_tokens, status_code, status_source in cls._charger_status_token_rules(svc, auto_mode_active):
            if tokens & hint_tokens:
                return status_code, status_source
        return None

    @classmethod
    def _charger_status_token_rules(
        cls,
        svc: Any,
        auto_mode_active: bool,
    ) -> tuple[tuple[set[str], int, str], ...]:
        return (
            (set(cls.CHARGER_STATUS_FINISHED_HINT_TOKENS), 3, "charger-status-finished"),
            (set(cls.CHARGER_STATUS_WAITING_HINT_TOKENS), 4 if auto_mode_active else 6, "charger-status-waiting"),
            (set(cls.CHARGER_STATUS_CHARGING_HINT_TOKENS), 2, "charger-status-charging"),
            (set(cls.CHARGER_STATUS_READY_HINT_TOKENS), int(getattr(svc, "idle_status", 1)), "charger-status-ready"),
        )

    @classmethod
    def _effective_enabled_state(cls, svc: Any, relay_on: bool, now: float | None = None) -> bool:
        charger_enabled = cls._fresh_charger_enabled_readback(svc, now)
        return bool(relay_on) if charger_enabled is None else bool(charger_enabled)

    @classmethod
    def _enable_control_source_key(cls, svc: Any) -> str:
        return "charger" if cls._charger_enable_backend(svc) is not None else "shelly"

    @classmethod
    def _enable_control_label(cls, svc: Any) -> str:
        return "charger backend" if cls._charger_enable_backend(svc) is not None else "Shelly relay"
