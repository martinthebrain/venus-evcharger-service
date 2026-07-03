# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled-mode and software-update diagnostic values."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts import (
    normalized_scheduled_state_fields,
    normalized_software_update_state_fields,
)
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_sources import _DbusDiagnosticsSources


class _DbusDiagnosticsSchedule(_DbusDiagnosticsSources):
    @classmethod
    def _scheduled_counter_values_from_snapshot(cls, scheduled_snapshot: Any) -> dict[str, str | int]:
        """Return normalized outward scheduled-state diagnostics."""
        if scheduled_snapshot is None:
            return cls._disabled_scheduled_counter_values()
        return cls._active_scheduled_counter_values(scheduled_snapshot)

    @staticmethod
    def _disabled_scheduled_counter_values() -> dict[str, str | int]:
        """Return outward scheduled-state diagnostics when no schedule is active."""
        scheduled_state, scheduled_state_code, scheduled_reason, scheduled_reason_code, scheduled_night_boost = (
            normalized_scheduled_state_fields(False, "disabled", 0, "disabled", 0, 0)
        )
        return {
            "auto_scheduled_state": scheduled_state,
            "auto_scheduled_state_code": scheduled_state_code,
            "auto_scheduled_reason": scheduled_reason,
            "auto_scheduled_reason_code": scheduled_reason_code,
            "auto_scheduled_night_boost_active": scheduled_night_boost,
            "auto_scheduled_target_day_enabled": 0,
            "auto_scheduled_target_day": "",
            "auto_scheduled_target_date": "",
            "auto_scheduled_fallback_start": "",
            "auto_scheduled_boost_until": "",
        }

    @staticmethod
    def _active_scheduled_counter_values(scheduled_snapshot: Any) -> dict[str, str | int]:
        """Return outward scheduled-state diagnostics for one active snapshot."""
        scheduled_state, scheduled_state_code, scheduled_reason, scheduled_reason_code, scheduled_night_boost = (
            normalized_scheduled_state_fields(
                True,
                scheduled_snapshot.state,
                scheduled_snapshot.state_code,
                scheduled_snapshot.reason,
                scheduled_snapshot.reason_code,
                int(bool(scheduled_snapshot.night_boost_active)),
            )
        )
        return {
            "auto_scheduled_state": scheduled_state,
            "auto_scheduled_state_code": scheduled_state_code,
            "auto_scheduled_reason": scheduled_reason,
            "auto_scheduled_reason_code": scheduled_reason_code,
            "auto_scheduled_night_boost_active": scheduled_night_boost,
            "auto_scheduled_target_day_enabled": int(bool(scheduled_snapshot.target_day_enabled)),
            "auto_scheduled_target_day": scheduled_snapshot.target_day_label,
            "auto_scheduled_target_date": scheduled_snapshot.target_date_text,
            "auto_scheduled_fallback_start": scheduled_snapshot.fallback_start_text,
            "auto_scheduled_boost_until": scheduled_snapshot.boost_until_text,
        }

    def _software_update_counter_values(self) -> dict[str, DiagnosticValue]:
        """Return normalized outward software-update diagnostics."""
        state, state_code, available, no_update = normalized_software_update_state_fields(
            getattr(self.service, "_software_update_state", "idle"),
            getattr(self.service, "_software_update_available", False),
            getattr(self.service, "_software_update_no_update_active", False),
        )
        return {
            "auto_software_update_available": available,
            "auto_software_update_state": state,
            "auto_software_update_state_code": state_code,
            "auto_software_update_detail": str(getattr(self.service, "_software_update_detail", "") or ""),
            "auto_software_update_current_version": str(getattr(self.service, "_software_update_current_version", "") or ""),
            "auto_software_update_available_version": str(
                getattr(self.service, "_software_update_available_version", "") or ""
            ),
            "auto_software_update_no_update_active": no_update,
        }
