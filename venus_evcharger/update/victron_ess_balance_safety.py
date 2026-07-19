# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias safety helpers."""

from __future__ import annotations

from typing import Any

from .victron_ess_balance_apply_pid import VictronEssPidController
from .victron_ess_balance_apply_sources import VictronEssSourceResolver
from .victron_ess_balance_safety_support import VictronEssSafetyRecovery


def _optional_numeric_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class VictronEssSafetyController:
    def __init__(
        self,
        sources: VictronEssSourceResolver,
        pid: VictronEssPidController,
        recovery: VictronEssSafetyRecovery,
    ) -> None:
        self._sources = sources
        self._pid = pid
        self._recovery = recovery

    def _populate_victron_ess_balance_runtime_safety_metrics(
        self,
        svc: Any,
        now: float,
        metrics: dict[str, Any],
    ) -> None:
        lockout_until = self._sources._optional_float(svc._victron_ess_balance_oscillation_lockout_until)
        metrics.update(self._victron_ess_balance_lockout_metrics(svc, now, lockout_until))
        metrics.update(self._victron_ess_balance_cooldown_metrics(svc, now))
        metrics.update(self._victron_ess_balance_auto_apply_suspend_metrics(svc, now))
        metrics.update(self._victron_ess_balance_safe_state_metrics(svc))

    def _victron_ess_balance_lockout_metrics(
        self,
        svc: Any,
        now: float,
        lockout_until: float | None,
    ) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": int(
                bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", True))
            ),
            "battery_discharge_balance_victron_bias_oscillation_lockout_active": int(
                bool(lockout_until is not None and float(now) < float(lockout_until))
            ),
            "battery_discharge_balance_victron_bias_oscillation_lockout_reason": str(
                getattr(svc, "_victron_ess_balance_oscillation_lockout_reason", None) or ""
            ),
            "battery_discharge_balance_victron_bias_oscillation_lockout_until": lockout_until,
            "battery_discharge_balance_victron_bias_oscillation_direction_change_count": int(
                self._victron_ess_balance_recent_direction_change_count(svc, now)
            ),
        }

    def _victron_ess_balance_cooldown_metrics(self, svc: Any, now: float) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active": int(
                self._recovery._victron_ess_balance_overshoot_cooldown_active(svc, now)
            ),
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": str(
                getattr(svc, "_victron_ess_balance_overshoot_cooldown_reason", None) or ""
            ),
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": self._sources._optional_float(
                getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
            ),
        }

    def _victron_ess_balance_auto_apply_suspend_metrics(self, svc: Any, now: float) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_auto_apply_suspend_active": int(
                self._recovery._victron_ess_balance_auto_apply_suspended(svc, now)
            ),
            "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": str(
                getattr(svc, "_victron_ess_balance_auto_apply_suspend_reason", None) or ""
            ),
            "battery_discharge_balance_victron_bias_auto_apply_suspend_until": self._sources._optional_float(
                getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None)
            ),
        }

    @staticmethod
    def _victron_ess_balance_safe_state_metrics(svc: Any) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_safe_state_active": int(
                bool(getattr(svc, "_victron_ess_balance_safe_state_active", None))
            ),
            "battery_discharge_balance_victron_bias_safe_state_reason": str(
                getattr(svc, "_victron_ess_balance_safe_state_reason", None) or ""
            ),
        }

    def _victron_ess_balance_telemetry_is_clean(
        self,
        svc: Any,
        cluster: dict[str, Any],
        source_error_w: float,
    ) -> tuple[bool, str]:
        precheck = self._victron_ess_balance_telemetry_precheck_reason(svc)
        if precheck is not None:
            return precheck
        telemetry_reason = self._victron_ess_balance_telemetry_window_reason(svc, cluster)
        if telemetry_reason is not None:
            return False, telemetry_reason
        if self._victron_ess_balance_error_inside_deadband(svc, source_error_w):
            return False, "error_inside_deadband"
        return True, "clean"

    def _victron_ess_balance_telemetry_window_reason(
        self,
        svc: Any,
        cluster: dict[str, Any],
    ) -> str | None:
        grid_reason = self._victron_ess_balance_grid_window_reason(svc, cluster)
        if grid_reason is not None:
            return grid_reason
        power_reason = self._victron_ess_balance_power_window_reason(svc, cluster)
        if power_reason is not None:
            return power_reason
        return self._victron_ess_balance_ev_window_reason(svc)

    def _victron_ess_balance_grid_window_reason(
        self,
        svc: Any,
        cluster: dict[str, Any],
    ) -> str | None:
        grid_interaction_w = self._sources._optional_float(cluster.get("battery_combined_grid_interaction_w"))
        if grid_interaction_w is None:
            return "grid_interaction_missing"
        last_grid_interaction_w = self._sources._optional_float(
            svc._victron_ess_balance_telemetry_last_grid_interaction_w
        )
        if self._victron_ess_balance_grid_interaction_unstable(grid_interaction_w, last_grid_interaction_w):
            return "grid_unstable"
        return None

    def _victron_ess_balance_power_window_reason(
        self,
        svc: Any,
        cluster: dict[str, Any],
    ) -> str | None:
        ac_power_w = self._sources._optional_float(cluster.get("battery_combined_ac_power_w"))
        last_ac_power_w = self._sources._optional_float(svc._victron_ess_balance_telemetry_last_ac_power_w)
        if self._victron_ess_balance_foreign_power_event(ac_power_w, last_ac_power_w):
            return "foreign_power_event"
        return None

    def _victron_ess_balance_ev_window_reason(self, svc: Any) -> str | None:
        ev_power_w = self._sources._victron_ess_balance_ev_power_w(svc)
        last_ev_power_w = self._sources._optional_float(svc._victron_ess_balance_telemetry_last_ev_power_w)
        if self._victron_ess_balance_ev_load_jump(ev_power_w, last_ev_power_w):
            return "ev_load_jump"
        return None

    @staticmethod
    def _victron_ess_balance_telemetry_precheck_reason(svc: Any) -> tuple[bool, str] | None:
        if not VictronEssSafetyController._victron_ess_balance_requires_clean_phases(svc):
            return True, "clean_not_required"
        cached_inputs_reason = VictronEssSafetyController._victron_ess_balance_cached_input_reason(svc)
        if cached_inputs_reason is not None:
            return False, cached_inputs_reason
        phase_switch_reason = VictronEssSafetyController._victron_ess_balance_phase_switch_reason(svc)
        if phase_switch_reason is not None:
            return False, phase_switch_reason
        contactor_reason = VictronEssSafetyController._victron_ess_balance_contactor_block_reason(svc)
        if contactor_reason is not None:
            return False, contactor_reason
        return None

    @staticmethod
    def _victron_ess_balance_requires_clean_phases(svc: Any) -> bool:
        return bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_require_clean_phases", True))

    @staticmethod
    def _victron_ess_balance_cached_input_reason(svc: Any) -> str | None:
        if bool(getattr(svc, "_auto_cached_inputs_used", None)):
            return "cached_inputs"
        return None

    @staticmethod
    def _victron_ess_balance_phase_switch_reason(svc: Any) -> str | None:
        if str(getattr(svc, "_phase_switch_state", None) or "").strip():
            return "phase_switch_active"
        return None

    @staticmethod
    def _victron_ess_balance_contactor_block_reason(svc: Any) -> str | None:
        if str(getattr(svc, "_contactor_fault_active_reason", None) or "").strip():
            return "contactor_fault_active"
        if str(getattr(svc, "_contactor_lockout_reason", None) or "").strip():
            return "contactor_lockout_active"
        return None

    @staticmethod
    def _victron_ess_balance_grid_interaction_unstable(
        grid_interaction_w: float | None,
        last_grid_interaction_w: float | None,
    ) -> bool:
        return (
            grid_interaction_w is not None
            and last_grid_interaction_w is not None
            and abs(float(grid_interaction_w) - float(last_grid_interaction_w)) > 600.0
        )

    @staticmethod
    def _victron_ess_balance_foreign_power_event(
        ac_power_w: float | None,
        last_ac_power_w: float | None,
    ) -> bool:
        return (
            ac_power_w is not None
            and last_ac_power_w is not None
            and abs(float(ac_power_w) - float(last_ac_power_w)) > 900.0
        )

    @staticmethod
    def _victron_ess_balance_ev_load_jump(
        ev_power_w: float | None,
        last_ev_power_w: float | None,
    ) -> bool:
        return (
            ev_power_w is not None
            and last_ev_power_w is not None
            and abs(float(ev_power_w) - float(last_ev_power_w)) > 500.0
        )

    @staticmethod
    def _victron_ess_balance_error_inside_deadband(svc: Any, source_error_w: float) -> bool:
        deadband_w = float(svc.auto_battery_discharge_balance_victron_bias_deadband_watts)
        return abs(float(source_error_w)) < max(10.0, deadband_w)

    @staticmethod
    def _victron_ess_balance_recent_direction_change_count(svc: Any, now: float) -> int:
        window_seconds = VictronEssSafetyController._victron_ess_balance_direction_change_window_seconds(svc)
        raw_entries = svc._victron_ess_balance_recent_action_changes
        entries = raw_entries if isinstance(raw_entries, list) else []
        cutoff = float(now) - window_seconds
        kept = VictronEssSafetyController._victron_ess_balance_kept_action_changes(entries, cutoff)
        svc._victron_ess_balance_recent_action_changes = kept
        return max(0, len(kept) - 1)

    @staticmethod
    def _victron_ess_balance_kept_action_changes(entries: list[Any], cutoff: float) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_at = _optional_numeric_float(entry.get("at"))
            if entry_at is None or float(entry_at) < cutoff:
                continue
            kept.append(dict(entry))
        return kept

    @staticmethod
    def _victron_ess_balance_direction_change_window_seconds(svc: Any) -> float:
        return max(
            0.0,
            float(
                getattr(
                    svc,
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds",
                    None,
                )
                or 120.0
            ),
        )

    def _victron_ess_balance_note_action_direction(self, svc: Any, action_direction: str, now: float) -> int:
        raw_action = str(action_direction).strip()
        if raw_action not in {"more_export", "less_export"}:
            return self._victron_ess_balance_recent_direction_change_count(svc, now)
        entries = self._victron_ess_balance_action_change_entries(svc)
        last_direction = self._victron_ess_balance_last_action_direction(svc)
        if self._victron_ess_balance_should_record_action_direction(entries, last_direction, raw_action):
            entries.append({"at": float(now), "action_direction": raw_action})
        svc._victron_ess_balance_last_action_direction = raw_action
        svc._victron_ess_balance_recent_action_changes = entries
        direction_change_count = self._victron_ess_balance_recent_direction_change_count(svc, now)
        if self._victron_ess_balance_should_enter_oscillation_lockout(svc, direction_change_count):
            duration_seconds = self._victron_ess_balance_oscillation_lockout_duration_seconds(svc)
            svc._victron_ess_balance_oscillation_lockout_until = float(now + duration_seconds)
            svc._victron_ess_balance_oscillation_lockout_reason = "direction_change_oscillation"
            self._pid.reset_integral(svc, aggressive=True)
        return direction_change_count

    @staticmethod
    def _victron_ess_balance_action_change_entries(svc: Any) -> list[Any]:
        entries = getattr(svc, "_victron_ess_balance_recent_action_changes", None)
        return entries if isinstance(entries, list) else []

    @staticmethod
    def _victron_ess_balance_last_action_direction(svc: Any) -> str:
        return str(getattr(svc, "_victron_ess_balance_last_action_direction", None) or "").strip()

    @staticmethod
    def _victron_ess_balance_should_record_action_direction(
        entries: list[Any],
        last_direction: str,
        raw_action: str,
    ) -> bool:
        return not entries or last_direction != raw_action

    @staticmethod
    def _victron_ess_balance_should_enter_oscillation_lockout(
        svc: Any,
        direction_change_count: int,
    ) -> bool:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", True)):
            return False
        min_direction_changes = max(
            1,
            int(
                getattr(
                    svc,
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes",
                    None,
                )
                or 3
            ),
        )
        return direction_change_count >= min_direction_changes

    @staticmethod
    def _victron_ess_balance_oscillation_lockout_duration_seconds(svc: Any) -> float:
        return max(
            0.0,
            float(
                getattr(
                    svc,
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds",
                    None,
                )
                or 180.0
            ),
        )

    def _victron_ess_balance_oscillation_lockout_active(self, svc: Any, now: float) -> bool:
        lockout_until = self._sources._optional_float(svc._victron_ess_balance_oscillation_lockout_until)
        return lockout_until is not None and float(now) < float(lockout_until)
