# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled-mode and software-update diagnostic values."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts import (
    normalized_scheduled_state_values,
    normalized_software_update_state_fields,
)
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_sources import _DbusDiagnosticsSources


_SCHEDULED_TEXT_FIELDS = (
    ("auto_scheduled_target_day", "target_day_label"),
    ("auto_scheduled_target_date", "target_date_text"),
    ("auto_scheduled_fallback_start", "fallback_start_text"),
    ("auto_scheduled_boost_until", "boost_until_text"),
)
_DISABLED_SCHEDULED_VALUES: dict[str, str | int] = {
    "auto_scheduled_state": "disabled",
    "auto_scheduled_state_code": 0,
    "auto_scheduled_reason": "disabled",
    "auto_scheduled_reason_code": 0,
    "auto_scheduled_night_boost_active": 0,
    "auto_scheduled_target_day_enabled": 0,
    "auto_scheduled_target_day": "",
    "auto_scheduled_target_date": "",
    "auto_scheduled_fallback_start": "",
    "auto_scheduled_boost_until": "",
}
_SOFTWARE_UPDATE_TEXT_FIELDS = (
    ("auto_software_update_detail", "_software_update_detail"),
    ("auto_software_update_current_version", "_software_update_current_version"),
    ("auto_software_update_available_version", "_software_update_available_version"),
)

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
        return dict(_DISABLED_SCHEDULED_VALUES)

    @classmethod
    def _active_scheduled_counter_values(cls, scheduled_snapshot: Any) -> dict[str, str | int]:
        """Return outward scheduled-state diagnostics for one active snapshot."""
        scheduled_state, scheduled_state_code, scheduled_reason, scheduled_reason_code, scheduled_night_boost = (
            normalized_scheduled_state_values(
                True,
                scheduled_snapshot.state,
                scheduled_snapshot.reason,
                int(bool(scheduled_snapshot.night_boost_active)),
            )
        )
        values: dict[str, str | int] = {
            "auto_scheduled_state": scheduled_state,
            "auto_scheduled_state_code": scheduled_state_code,
            "auto_scheduled_reason": scheduled_reason,
            "auto_scheduled_reason_code": scheduled_reason_code,
            "auto_scheduled_night_boost_active": scheduled_night_boost,
            "auto_scheduled_target_day_enabled": int(bool(scheduled_snapshot.target_day_enabled)),
        }
        for output_key, attribute_name in _SCHEDULED_TEXT_FIELDS:
            values[output_key] = cls._text_attribute(scheduled_snapshot, attribute_name)
        return values

    def _software_update_counter_values(self) -> dict[str, DiagnosticValue]:
        """Return normalized outward software-update diagnostics."""
        state, state_code, available, no_update = normalized_software_update_state_fields(
            self._raw_attribute(self.service, "_software_update_state"),
            self._raw_attribute(self.service, "_software_update_available"),
            self._raw_attribute(self.service, "_software_update_no_update_active"),
        )
        values: dict[str, DiagnosticValue] = {
            "auto_software_update_state": state,
            "auto_software_update_state_code": state_code,
            "auto_software_update_available": available,
            "auto_software_update_no_update_active": no_update,
        }
        for output_key, attribute_name in _SOFTWARE_UPDATE_TEXT_FIELDS:
            values[output_key] = self._text_attribute(self.service, attribute_name)
        return values
