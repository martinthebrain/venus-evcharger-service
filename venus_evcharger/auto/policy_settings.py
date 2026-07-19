# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed runtime-setting boundary for the canonical :class:`AutoPolicy`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from venus_evcharger.auto.policy import AutoPolicy

PolicyValue: TypeAlias = bool | float | int
PolicyValueKind: TypeAlias = Literal["bool", "float", "int"]
PolicyGetter: TypeAlias = Callable[[AutoPolicy], PolicyValue]
PolicySetter: TypeAlias = Callable[[AutoPolicy, PolicyValue], None]


def _scalar_input(value: object) -> bool | float | int | str:
    if isinstance(value, (bool, float, int, str)):
        return value
    raise TypeError(f"Auto policy setting requires a scalar value, got {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class AutoPolicySetting:
    """One externally writable setting backed directly by ``AutoPolicy``."""

    dbus_path: str
    config_key: str
    value_kind: PolicyValueKind
    getter: PolicyGetter
    setter: PolicySetter

    def normalize(self, raw_value: object) -> PolicyValue:
        """Normalize one transport value with the historical DBus-write rules."""
        scalar = _scalar_input(raw_value)
        if self.value_kind == "bool":
            return bool(int(scalar) > 0)
        if self.value_kind == "int":
            return int(scalar)
        return float(scalar)

    def read(self, policy: AutoPolicy) -> PolicyValue:
        """Read the canonical value in its outward-facing representation."""
        value = self.getter(policy)
        return int(value) if self.value_kind == "bool" else value

    def update(self, policy: AutoPolicy, raw_value: object) -> PolicyValue:
        """Update the canonical policy and return the normalized stored value."""
        self.setter(policy, self.normalize(raw_value))
        policy.clamp()
        return self.read(policy)


AUTO_POLICY_SETTINGS: tuple[AutoPolicySetting, ...] = (
    AutoPolicySetting(
        "/Auto/StartSurplusWatts",
        "AutoStartSurplusWatts",
        "float",
        lambda policy: policy.normal_profile.start_surplus_watts,
        lambda policy, value: setattr(policy.normal_profile, "start_surplus_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/StopSurplusWatts",
        "AutoStopSurplusWatts",
        "float",
        lambda policy: policy.normal_profile.stop_surplus_watts,
        lambda policy, value: setattr(policy.normal_profile, "stop_surplus_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/MinSoc",
        "AutoMinSoc",
        "float",
        lambda policy: policy.min_soc,
        lambda policy, value: setattr(policy, "min_soc", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/ResumeSoc",
        "AutoResumeSoc",
        "float",
        lambda policy: policy.resume_soc,
        lambda policy, value: setattr(policy, "resume_soc", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/GridRecoveryStartSeconds",
        "AutoGridRecoveryStartSeconds",
        "float",
        lambda policy: policy.grid_recovery_start_seconds,
        lambda policy, value: setattr(policy, "grid_recovery_start_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/StopSurplusDelaySeconds",
        "AutoStopSurplusDelaySeconds",
        "float",
        lambda policy: policy.stop_surplus_delay_seconds,
        lambda policy, value: setattr(policy, "stop_surplus_delay_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/StopSurplusVolatilityLowWatts",
        "AutoStopSurplusVolatilityLowWatts",
        "float",
        lambda policy: policy.ewma.volatility_low_watts,
        lambda policy, value: setattr(policy.ewma, "volatility_low_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/StopSurplusVolatilityHighWatts",
        "AutoStopSurplusVolatilityHighWatts",
        "float",
        lambda policy: policy.ewma.volatility_high_watts,
        lambda policy, value: setattr(policy.ewma, "volatility_high_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/ReferenceChargePowerWatts",
        "AutoReferenceChargePowerWatts",
        "float",
        lambda policy: policy.learn_charge_power.reference_power_watts,
        lambda policy, value: setattr(policy.learn_charge_power, "reference_power_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/LearnChargePowerEnabled",
        "AutoLearnChargePower",
        "bool",
        lambda policy: policy.learn_charge_power.enabled,
        lambda policy, value: setattr(policy.learn_charge_power, "enabled", bool(value)),
    ),
    AutoPolicySetting(
        "/Auto/LearnChargePowerMinWatts",
        "AutoLearnChargePowerMinWatts",
        "float",
        lambda policy: policy.learn_charge_power.min_watts,
        lambda policy, value: setattr(policy.learn_charge_power, "min_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/LearnChargePowerAlpha",
        "AutoLearnChargePowerAlpha",
        "float",
        lambda policy: policy.learn_charge_power.alpha,
        lambda policy, value: setattr(policy.learn_charge_power, "alpha", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/LearnChargePowerStartDelaySeconds",
        "AutoLearnChargePowerStartDelaySeconds",
        "float",
        lambda policy: policy.learn_charge_power.start_delay_seconds,
        lambda policy, value: setattr(policy.learn_charge_power, "start_delay_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/LearnChargePowerWindowSeconds",
        "AutoLearnChargePowerWindowSeconds",
        "float",
        lambda policy: policy.learn_charge_power.window_seconds,
        lambda policy, value: setattr(policy.learn_charge_power, "window_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/LearnChargePowerMaxAgeSeconds",
        "AutoLearnChargePowerMaxAgeSeconds",
        "float",
        lambda policy: policy.learn_charge_power.max_age_seconds,
        lambda policy, value: setattr(policy.learn_charge_power, "max_age_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseSwitching",
        "AutoPhaseSwitching",
        "bool",
        lambda policy: policy.phase.enabled,
        lambda policy, value: setattr(policy.phase, "enabled", bool(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhasePreferLowestWhenIdle",
        "AutoPhasePreferLowestWhenIdle",
        "bool",
        lambda policy: policy.phase.prefer_lowest_phase_when_idle,
        lambda policy, value: setattr(policy.phase, "prefer_lowest_phase_when_idle", bool(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseUpshiftDelaySeconds",
        "AutoPhaseUpshiftDelaySeconds",
        "float",
        lambda policy: policy.phase.upshift_delay_seconds,
        lambda policy, value: setattr(policy.phase, "upshift_delay_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseDownshiftDelaySeconds",
        "AutoPhaseDownshiftDelaySeconds",
        "float",
        lambda policy: policy.phase.downshift_delay_seconds,
        lambda policy, value: setattr(policy.phase, "downshift_delay_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseUpshiftHeadroomWatts",
        "AutoPhaseUpshiftHeadroomWatts",
        "float",
        lambda policy: policy.phase.upshift_headroom_watts,
        lambda policy, value: setattr(policy.phase, "upshift_headroom_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseDownshiftMarginWatts",
        "AutoPhaseDownshiftMarginWatts",
        "float",
        lambda policy: policy.phase.downshift_margin_watts,
        lambda policy, value: setattr(policy.phase, "downshift_margin_watts", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseMismatchRetrySeconds",
        "AutoPhaseMismatchRetrySeconds",
        "float",
        lambda policy: policy.phase.mismatch_retry_seconds,
        lambda policy, value: setattr(policy.phase, "mismatch_retry_seconds", float(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseMismatchLockoutCount",
        "AutoPhaseMismatchLockoutCount",
        "int",
        lambda policy: policy.phase.mismatch_lockout_count,
        lambda policy, value: setattr(policy.phase, "mismatch_lockout_count", int(value)),
    ),
    AutoPolicySetting(
        "/Auto/PhaseMismatchLockoutSeconds",
        "AutoPhaseMismatchLockoutSeconds",
        "float",
        lambda policy: policy.phase.mismatch_lockout_seconds,
        lambda policy, value: setattr(policy.phase, "mismatch_lockout_seconds", float(value)),
    ),
)

AUTO_POLICY_SETTING_BY_PATH: dict[str, AutoPolicySetting] = {
    setting.dbus_path: setting for setting in AUTO_POLICY_SETTINGS
}
AUTO_POLICY_SETTING_BY_CONFIG_KEY: dict[str, AutoPolicySetting] = {
    setting.config_key: setting for setting in AUTO_POLICY_SETTINGS
}


def auto_policy_control_values(policy: AutoPolicy) -> dict[str, PolicyValue]:
    """Return all writable Auto-policy values keyed by their control path."""
    return {setting.dbus_path: setting.read(policy) for setting in AUTO_POLICY_SETTINGS}
