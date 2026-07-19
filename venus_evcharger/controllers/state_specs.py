# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared state-controller specs and parser helpers."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from typing import Literal, TypeAlias

from venus_evcharger.auto.policy_settings import AUTO_POLICY_SETTING_BY_PATH, AutoPolicySetting


OverrideValueKind: TypeAlias = Literal["bool", "float", "hhmm", "int", "phase", "weekday_set"]


@dataclass(frozen=True)
class RuntimeOverrideSpec:
    """One DBus-writable runtime setting that can persist in an override file."""

    dbus_path: str
    config_key: str
    attr_name: str | None
    value_kind: OverrideValueKind
    policy_setting: AutoPolicySetting | None = None


def _policy_runtime_override(dbus_path: str) -> RuntimeOverrideSpec:
    setting = AUTO_POLICY_SETTING_BY_PATH[dbus_path]
    return RuntimeOverrideSpec(
        dbus_path=setting.dbus_path,
        config_key=setting.config_key,
        attr_name=None,
        value_kind=setting.value_kind,
        policy_setting=setting,
    )


RUNTIME_OVERRIDE_SPECS: tuple[RuntimeOverrideSpec, ...] = (
    RuntimeOverrideSpec("/Mode", "Mode", "virtual_mode", "int"),
    RuntimeOverrideSpec("/AutoStart", "AutoStart", "virtual_autostart", "bool"),
    RuntimeOverrideSpec("/SetCurrent", "SetCurrent", "virtual_set_current", "float"),
    RuntimeOverrideSpec("/MinCurrent", "MinCurrent", "min_current", "float"),
    RuntimeOverrideSpec("/MaxCurrent", "MaxCurrent", "max_current", "float"),
    RuntimeOverrideSpec("/PhaseSelection", "PhaseSelection", "requested_phase_selection", "phase"),
    _policy_runtime_override("/Auto/StartSurplusWatts"),
    _policy_runtime_override("/Auto/StopSurplusWatts"),
    _policy_runtime_override("/Auto/MinSoc"),
    _policy_runtime_override("/Auto/ResumeSoc"),
    RuntimeOverrideSpec("/Auto/StartDelaySeconds", "AutoStartDelaySeconds", "auto_start_delay_seconds", "float"),
    RuntimeOverrideSpec("/Auto/StopDelaySeconds", "AutoStopDelaySeconds", "auto_stop_delay_seconds", "float"),
    RuntimeOverrideSpec("/Auto/ScheduledEnabledDays", "AutoScheduledEnabledDays", "auto_scheduled_enabled_days", "weekday_set"),
    RuntimeOverrideSpec(
        "/Auto/ScheduledFallbackDelaySeconds",
        "AutoScheduledNightStartDelaySeconds",
        "auto_scheduled_night_start_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/ScheduledLatestEndTime",
        "AutoScheduledLatestEndTime",
        "auto_scheduled_latest_end_time",
        "hhmm",
    ),
    RuntimeOverrideSpec(
        "/Auto/ScheduledNightCurrent",
        "AutoScheduledNightCurrentAmps",
        "auto_scheduled_night_current_amps",
        "float",
    ),
    RuntimeOverrideSpec("/Auto/DbusBackoffBaseSeconds", "AutoDbusBackoffBaseSeconds", "auto_dbus_backoff_base_seconds", "float"),
    RuntimeOverrideSpec("/Auto/DbusBackoffMaxSeconds", "AutoDbusBackoffMaxSeconds", "auto_dbus_backoff_max_seconds", "float"),
    _policy_runtime_override("/Auto/GridRecoveryStartSeconds"),
    _policy_runtime_override("/Auto/StopSurplusDelaySeconds"),
    _policy_runtime_override("/Auto/StopSurplusVolatilityLowWatts"),
    _policy_runtime_override("/Auto/StopSurplusVolatilityHighWatts"),
    _policy_runtime_override("/Auto/ReferenceChargePowerWatts"),
    _policy_runtime_override("/Auto/LearnChargePowerEnabled"),
    _policy_runtime_override("/Auto/LearnChargePowerMinWatts"),
    _policy_runtime_override("/Auto/LearnChargePowerAlpha"),
    _policy_runtime_override("/Auto/LearnChargePowerStartDelaySeconds"),
    _policy_runtime_override("/Auto/LearnChargePowerWindowSeconds"),
    _policy_runtime_override("/Auto/LearnChargePowerMaxAgeSeconds"),
    _policy_runtime_override("/Auto/PhaseSwitching"),
    _policy_runtime_override("/Auto/PhasePreferLowestWhenIdle"),
    _policy_runtime_override("/Auto/PhaseUpshiftDelaySeconds"),
    _policy_runtime_override("/Auto/PhaseDownshiftDelaySeconds"),
    _policy_runtime_override("/Auto/PhaseUpshiftHeadroomWatts"),
    _policy_runtime_override("/Auto/PhaseDownshiftMarginWatts"),
    _policy_runtime_override("/Auto/PhaseMismatchRetrySeconds"),
    _policy_runtime_override("/Auto/PhaseMismatchLockoutCount"),
    _policy_runtime_override("/Auto/PhaseMismatchLockoutSeconds"),
)

RUNTIME_OVERRIDE_BY_PATH: dict[str, RuntimeOverrideSpec] = {
    spec.dbus_path: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_BY_CONFIG_KEY: dict[str, RuntimeOverrideSpec] = {
    spec.config_key: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_SECTION = "RuntimeOverrides"


class CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr
