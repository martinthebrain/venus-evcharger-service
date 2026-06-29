# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-metadata, relay-sync, and PM publish helpers for the update cycle."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from venus_evcharger.update.relay_phase_switch_runtime import _RelayPhaseSwitchRuntime

RELAY_PLACEHOLDER_PUBLISH_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class _RelayPhasePublish(_RelayPhaseSwitchRuntime):
    """Translate PM metadata into phase displays and track relay confirmations."""

    if TYPE_CHECKING:  # pragma: no cover
        _phase_values: Callable[[float, float, Any, Any], dict[str, Any]]

    @staticmethod
    def _phase_tuple(raw_value: Any) -> tuple[float, float, float] | None:
        if not isinstance(raw_value, (tuple, list)) or len(raw_value) != 3:
            return None
        values: tuple[float | None, float | None, float | None] = (
            _RelayPhasePublish._phase_tuple_item(raw_value[0]),
            _RelayPhasePublish._phase_tuple_item(raw_value[1]),
            _RelayPhasePublish._phase_tuple_item(raw_value[2]),
        )
        return _RelayPhasePublish._resolved_phase_tuple(values)

    @staticmethod
    def _phase_tuple_item(raw_value: Any) -> float | None:
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
            return None
        return float(raw_value)

    @staticmethod
    def _resolved_phase_tuple(
        values: tuple[float | None, float | None, float | None],
    ) -> tuple[float, float, float] | None:
        first, second, third = values
        if first is None or second is None or third is None:
            return None
        return first, second, third

    @staticmethod
    def _phase_voltage(voltage: float, selection: Any, voltage_mode: Any) -> float:
        normalized_selection = _RelayPhasePublish._normalized_phase_selection(selection)
        normalized_voltage_mode = _RelayPhasePublish._normalized_voltage_mode(voltage_mode)
        if not _RelayPhasePublish._selection_uses_line_to_line_voltage(normalized_selection, normalized_voltage_mode):
            return float(voltage)
        return float(voltage) / math.sqrt(3.0) if float(voltage) > 0.0 else 0.0

    @staticmethod
    def _normalized_phase_selection(selection: Any) -> str:
        return str(selection).strip().upper() if selection is not None else ""

    @staticmethod
    def _normalized_voltage_mode(voltage_mode: Any) -> str:
        return str(voltage_mode).strip().lower() if voltage_mode is not None else "phase"

    @staticmethod
    def _selection_uses_line_to_line_voltage(selection: str, voltage_mode: str) -> bool:
        return selection == "P1_P2_P3" and voltage_mode != "phase"

    def _phase_data_for_pm_status(
        self,
        pm_status: dict[str, Any] | None,
        power: float,
        voltage: float,
        current: float,
    ) -> dict[str, dict[str, float]]:
        svc = self.service
        phase_data = self._phase_data_from_backend_metadata(pm_status, voltage, getattr(svc, "voltage_mode", "phase"))
        if phase_data is not None:
            return phase_data
        return self._checked_phase_data(self._phase_values(power, voltage, svc.phase, svc.voltage_mode))

    @staticmethod
    def _checked_phase_data(value: Any) -> dict[str, dict[str, float]]:
        if not isinstance(value, dict):
            raise TypeError(f"_phase_values must return dict, got {type(value).__name__}")
        checked: dict[str, dict[str, float]] = {}
        for phase_name, phase_values in value.items():
            if not isinstance(phase_name, str) or not isinstance(phase_values, dict):
                raise TypeError("_phase_values must return dict[str, dict[str, float]]")
            checked[phase_name] = _RelayPhasePublish._checked_phase_values(phase_values)
        return checked

    @staticmethod
    def _checked_phase_values(values: dict[Any, Any]) -> dict[str, float]:
        checked: dict[str, float] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError("_phase_values must return dict[str, dict[str, float]]")
            checked[key] = float(value)
        return checked

    def _phase_data_from_backend_metadata(
        self,
        pm_status: dict[str, Any] | None,
        voltage: float,
        voltage_mode: Any,
    ) -> dict[str, dict[str, float]] | None:
        if not isinstance(pm_status, dict):
            return None
        phase_powers = self._phase_tuple(pm_status.get("_phase_powers_w"))
        if phase_powers is None:
            return None
        phase_currents = self._phase_tuple(pm_status.get("_phase_currents_a"))
        phase_voltage = self._phase_voltage(voltage, pm_status.get("_phase_selection"), voltage_mode)
        return self._phase_data_from_phase_tuples(phase_powers, phase_currents, phase_voltage)

    @staticmethod
    def _phase_measurement(
        phase_power: float,
        phase_current: float | None,
        phase_voltage: float,
    ) -> dict[str, float]:
        resolved_current = (
            float(phase_current)
            if phase_current is not None
            else (float(phase_power) / phase_voltage if phase_voltage else 0.0)
        )
        return {"power": float(phase_power), "voltage": phase_voltage, "current": resolved_current}

    def _phase_data_from_phase_tuples(
        self,
        phase_powers: tuple[float, float, float],
        phase_currents: tuple[float, float, float] | None,
        phase_voltage: float,
    ) -> dict[str, dict[str, float]]:
        phase_data: dict[str, dict[str, float]] = {}
        for phase_name, phase_power, phase_current in zip(
            ("L1", "L2", "L3"),
            phase_powers,
            phase_currents or (None, None, None),
        ):
            phase_data[phase_name] = self._phase_measurement(phase_power, phase_current, phase_voltage)
        return phase_data

    @staticmethod
    def log_auto_relay_change(svc: Any, desired_relay: bool) -> None:
        metrics = svc._last_auto_metrics
        logging.info(
            "Auto relay %s reason=%s surplus=%sW grid=%sW soc=%s%%",
            "ON" if desired_relay else "OFF",
            svc._last_health_reason,
            f"{metrics.get('surplus'):.0f}" if metrics.get("surplus") is not None else "na",
            f"{metrics.get('grid'):.0f}" if metrics.get("grid") is not None else "na",
            f"{metrics.get('soc'):.1f}" if metrics.get("soc") is not None else "na",
        )

    @staticmethod
    def _clear_relay_sync_tracking(svc: Any) -> None:
        svc._relay_sync_expected_state = None
        svc._relay_sync_requested_at = None
        svc._relay_sync_deadline_at = None
        svc._relay_sync_failure_reported = False

    @staticmethod
    def _pm_status_confirmed(pm_status: dict[str, Any]) -> bool:
        return bool(pm_status.get("_pm_confirmed", False))

    def _publish_local_pm_status_best_effort(self, relay_on: bool, now: float) -> None:
        svc = self.service
        try:
            svc._publish_local_pm_status(relay_on, now)
        except RELAY_PLACEHOLDER_PUBLISH_ERRORS as error:
            svc._warning_throttled(
                "relay-placeholder-publish-failed",
                max(1.0, float(getattr(svc, "relay_sync_timeout_seconds", 2.0) or 2.0)),
                "Local relay placeholder publish failed after queueing relay=%s: %s",
                int(bool(relay_on)),
                error,
                exc_info=error,
            )

    def relay_sync_health_override(self, relay_on: bool, pm_confirmed: bool, now: float) -> str | None:
        svc = self.service
        expected_state = getattr(svc, "_relay_sync_expected_state", None)
        if expected_state is None:
            return None
        expected_relay = bool(expected_state)
        if self._relay_sync_confirmed_match(svc, relay_on, pm_confirmed, expected_relay):
            return None
        deadline_at = getattr(svc, "_relay_sync_deadline_at", None)
        if self._relay_sync_before_deadline(deadline_at, now):
            return self._relay_sync_pre_timeout_result(relay_on, pm_confirmed, expected_relay)
        self._record_relay_sync_timeout(svc, relay_on, pm_confirmed, expected_relay, deadline_at)
        self._clear_relay_sync_tracking(svc)
        return "relay-sync-failed"

    def _relay_sync_confirmed_match(
        self,
        svc: Any,
        relay_on: bool,
        pm_confirmed: bool,
        expected_relay: bool,
    ) -> bool:
        if not pm_confirmed or bool(relay_on) != expected_relay:
            return False
        if getattr(svc, "_relay_sync_failure_reported", False):
            svc._mark_recovery("shelly", "Shelly relay confirmation recovered")
        self._clear_relay_sync_tracking(svc)
        return True

    @staticmethod
    def _relay_sync_before_deadline(deadline_at: Any, now: float) -> bool:
        return deadline_at is None or float(now) < float(deadline_at)

    @staticmethod
    def _relay_sync_pre_timeout_result(
        relay_on: bool,
        pm_confirmed: bool,
        expected_relay: bool,
    ) -> str | None:
        if pm_confirmed and bool(relay_on) != expected_relay:
            return "command-mismatch"
        return None

    def _record_relay_sync_timeout(
        self,
        svc: Any,
        relay_on: bool,
        pm_confirmed: bool,
        expected_relay: bool,
        deadline_at: Any,
    ) -> None:
        if getattr(svc, "_relay_sync_failure_reported", False):
            return
        svc._relay_sync_failure_reported = True
        timeout_seconds = max(0.0, float(deadline_at) - float(getattr(svc, "_relay_sync_requested_at", deadline_at)))
        svc._mark_failure("shelly")
        svc._warning_throttled(
            "relay-sync-failed",
            max(1.0, timeout_seconds),
            "Shelly relay state did not confirm to %s within %.1fs (actual=%s confirmed=%s)",
            expected_relay,
            timeout_seconds,
            bool(relay_on),
            int(bool(pm_confirmed)),
        )
