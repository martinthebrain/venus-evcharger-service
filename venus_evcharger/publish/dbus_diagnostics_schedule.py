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
            "/Auto/ScheduledState": scheduled_state,
            "/Auto/ScheduledStateCode": scheduled_state_code,
            "/Auto/ScheduledReason": scheduled_reason,
            "/Auto/ScheduledReasonCode": scheduled_reason_code,
            "/Auto/ScheduledNightBoostActive": scheduled_night_boost,
            "/Auto/ScheduledTargetDayEnabled": 0,
            "/Auto/ScheduledTargetDay": "",
            "/Auto/ScheduledTargetDate": "",
            "/Auto/ScheduledFallbackStart": "",
            "/Auto/ScheduledBoostUntil": "",
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
            "/Auto/ScheduledState": scheduled_state,
            "/Auto/ScheduledStateCode": scheduled_state_code,
            "/Auto/ScheduledReason": scheduled_reason,
            "/Auto/ScheduledReasonCode": scheduled_reason_code,
            "/Auto/ScheduledNightBoostActive": scheduled_night_boost,
            "/Auto/ScheduledTargetDayEnabled": int(bool(scheduled_snapshot.target_day_enabled)),
            "/Auto/ScheduledTargetDay": scheduled_snapshot.target_day_label,
            "/Auto/ScheduledTargetDate": scheduled_snapshot.target_date_text,
            "/Auto/ScheduledFallbackStart": scheduled_snapshot.fallback_start_text,
            "/Auto/ScheduledBoostUntil": scheduled_snapshot.boost_until_text,
        }

    def _software_update_counter_values(self) -> dict[str, DiagnosticValue]:
        """Return normalized outward software-update diagnostics."""
        state, state_code, available, no_update = normalized_software_update_state_fields(
            getattr(self.service, "_software_update_state", "idle"),
            getattr(self.service, "_software_update_available", False),
            getattr(self.service, "_software_update_no_update_active", False),
        )
        return {
            "/Auto/SoftwareUpdateAvailable": available,
            "/Auto/SoftwareUpdateState": state,
            "/Auto/SoftwareUpdateStateCode": state_code,
            "/Auto/SoftwareUpdateDetail": str(getattr(self.service, "_software_update_detail", "") or ""),
            "/Auto/SoftwareUpdateCurrentVersion": str(getattr(self.service, "_software_update_current_version", "") or ""),
            "/Auto/SoftwareUpdateAvailableVersion": str(
                getattr(self.service, "_software_update_available_version", "") or ""
            ),
            "/Auto/SoftwareUpdateNoUpdateActive": no_update,
        }
