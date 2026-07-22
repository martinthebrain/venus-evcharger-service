# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared state-controller specs and parser helpers."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from typing import Literal, TypeAlias

from venus_evcharger.auto.policy_settings import AUTO_POLICY_SETTING_BY_TARGET, AutoPolicySetting


OverrideValueKind: TypeAlias = Literal["bool", "float", "hhmm", "int", "phase", "weekday_set"]


@dataclass(frozen=True)
class RuntimeOverrideSpec:
    """One externally writable runtime setting persisted by semantic target."""

    target: str
    config_key: str
    attr_name: str | None
    value_kind: OverrideValueKind
    policy_setting: AutoPolicySetting | None = None


def _policy_runtime_override(target: str) -> RuntimeOverrideSpec:
    setting = AUTO_POLICY_SETTING_BY_TARGET[target]
    return RuntimeOverrideSpec(
        target=setting.target,
        config_key=setting.config_key,
        attr_name=None,
        value_kind=setting.value_kind,
        policy_setting=setting,
    )


RUNTIME_OVERRIDE_SPECS: tuple[RuntimeOverrideSpec, ...] = (
    RuntimeOverrideSpec("mode", "Mode", "virtual_mode", "int"),
    RuntimeOverrideSpec("auto_start", "AutoStart", "virtual_autostart", "bool"),
    RuntimeOverrideSpec("set_current", "SetCurrent", "virtual_set_current", "float"),
    RuntimeOverrideSpec("min_current", "MinCurrent", "min_current", "float"),
    RuntimeOverrideSpec("max_current", "MaxCurrent", "max_current", "float"),
    RuntimeOverrideSpec("phase_selection", "PhaseSelection", "requested_phase_selection", "phase"),
    _policy_runtime_override("auto_start_surplus_watts"),
    _policy_runtime_override("auto_stop_surplus_watts"),
    _policy_runtime_override("auto_min_soc"),
    _policy_runtime_override("auto_resume_soc"),
    RuntimeOverrideSpec("auto_start_delay_seconds", "AutoStartDelaySeconds", "auto_start_delay_seconds", "float"),
    RuntimeOverrideSpec("auto_stop_delay_seconds", "AutoStopDelaySeconds", "auto_stop_delay_seconds", "float"),
    RuntimeOverrideSpec("auto_scheduled_enabled_days", "AutoScheduledEnabledDays", "auto_scheduled_enabled_days", "weekday_set"),
    RuntimeOverrideSpec(
        "auto_scheduled_fallback_delay_seconds",
        "AutoScheduledNightStartDelaySeconds",
        "auto_scheduled_night_start_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "auto_scheduled_latest_end_time",
        "AutoScheduledLatestEndTime",
        "auto_scheduled_latest_end_time",
        "hhmm",
    ),
    RuntimeOverrideSpec(
        "auto_scheduled_night_current",
        "AutoScheduledNightCurrentAmps",
        "auto_scheduled_night_current_amps",
        "float",
    ),
    RuntimeOverrideSpec("auto_dbus_backoff_base_seconds", "AutoDbusBackoffBaseSeconds", "auto_dbus_backoff_base_seconds", "float"),
    RuntimeOverrideSpec("auto_dbus_backoff_max_seconds", "AutoDbusBackoffMaxSeconds", "auto_dbus_backoff_max_seconds", "float"),
    _policy_runtime_override("auto_grid_recovery_start_seconds"),
    _policy_runtime_override("auto_stop_surplus_delay_seconds"),
    _policy_runtime_override("auto_stop_surplus_volatility_low_watts"),
    _policy_runtime_override("auto_stop_surplus_volatility_high_watts"),
    _policy_runtime_override("auto_reference_charge_power_watts"),
    _policy_runtime_override("auto_learn_charge_power_enabled"),
    _policy_runtime_override("auto_learn_charge_power_min_watts"),
    _policy_runtime_override("auto_learn_charge_power_alpha"),
    _policy_runtime_override("auto_learn_charge_power_start_delay_seconds"),
    _policy_runtime_override("auto_learn_charge_power_window_seconds"),
    _policy_runtime_override("auto_learn_charge_power_max_age_seconds"),
    _policy_runtime_override("auto_phase_switching"),
    _policy_runtime_override("auto_phase_prefer_lowest_when_idle"),
    _policy_runtime_override("auto_phase_upshift_delay_seconds"),
    _policy_runtime_override("auto_phase_downshift_delay_seconds"),
    _policy_runtime_override("auto_phase_upshift_headroom_watts"),
    _policy_runtime_override("auto_phase_downshift_margin_watts"),
    _policy_runtime_override("auto_phase_mismatch_retry_seconds"),
    _policy_runtime_override("auto_phase_mismatch_lockout_count"),
    _policy_runtime_override("auto_phase_mismatch_lockout_seconds"),
)

RUNTIME_OVERRIDE_BY_TARGET: dict[str, RuntimeOverrideSpec] = {
    spec.target: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_BY_CONFIG_KEY: dict[str, RuntimeOverrideSpec] = {
    spec.config_key: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_SECTION = "RuntimeOverrides"


class CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr
