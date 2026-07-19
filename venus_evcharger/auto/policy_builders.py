# SPDX-License-Identifier: GPL-3.0-or-later
"""Builder helpers for loading and validating the canonical Auto policy."""

from __future__ import annotations

from configparser import SectionProxy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from venus_evcharger.auto.policy import AutoPolicy  # pragma: no cover


def _config_value(defaults: SectionProxy, key: str, fallback: Any) -> str:
    return defaults.get(key, str(fallback))


def build_auto_policy_from_config(policy_cls: type["AutoPolicy"], defaults: SectionProxy) -> "AutoPolicy":
    from venus_evcharger.auto.policy import AutoLearnChargePowerPolicy, AutoPhasePolicy, AutoStopEwmaPolicy, AutoThresholdProfile

    normal_start = float(_config_value(defaults, "AutoStartSurplusWatts", 1500))
    normal_stop = float(_config_value(defaults, "AutoStopSurplusWatts", normal_start - 400))
    min_soc = float(_config_value(defaults, "AutoMinSoc", 30))
    return policy_cls(
        normal_profile=AutoThresholdProfile(normal_start, normal_stop),
        high_soc_profile=AutoThresholdProfile(
            float(_config_value(defaults, "AutoHighSocStartSurplusWatts", normal_start)),
            float(_config_value(defaults, "AutoHighSocStopSurplusWatts", normal_stop)),
        ),
        high_soc_threshold=float(_config_value(defaults, "AutoHighSocThreshold", 50)),
        high_soc_release_threshold=float(
            _config_value(defaults, "AutoHighSocReleaseThreshold", _config_value(defaults, "AutoHighSocThreshold", 50))
        ),
        min_soc=min_soc,
        resume_soc=float(_config_value(defaults, "AutoResumeSoc", min_soc + 3)),
        start_max_grid_import_watts=float(_config_value(defaults, "AutoStartMaxGridImportWatts", 50)),
        stop_grid_import_watts=float(_config_value(defaults, "AutoStopGridImportWatts", 300)),
        grid_recovery_start_seconds=float(
            _config_value(defaults, "AutoGridRecoveryStartSeconds", _config_value(defaults, "AutoStartDelaySeconds", 10))
        ),
        stop_surplus_delay_seconds=float(
            _config_value(defaults, "AutoStopSurplusDelaySeconds", _config_value(defaults, "AutoStopDelaySeconds", 10))
        ),
        ewma=AutoStopEwmaPolicy(
            base_alpha=float(_config_value(defaults, "AutoStopEwmaAlpha", 0.35)),
            stable_alpha=float(_config_value(defaults, "AutoStopEwmaAlphaStable", 0.55)),
            volatile_alpha=float(_config_value(defaults, "AutoStopEwmaAlphaVolatile", 0.15)),
            volatility_low_watts=float(_config_value(defaults, "AutoStopSurplusVolatilityLowWatts", 150)),
            volatility_high_watts=float(_config_value(defaults, "AutoStopSurplusVolatilityHighWatts", 400)),
        ),
        learn_charge_power=AutoLearnChargePowerPolicy(
            enabled=defaults.get("AutoLearnChargePower", "1").strip().lower() in ("1", "true", "yes", "on"),
            reference_power_watts=float(_config_value(defaults, "AutoReferenceChargePowerWatts", 1900)),
            min_watts=float(_config_value(defaults, "AutoLearnChargePowerMinWatts", 500)),
            alpha=float(_config_value(defaults, "AutoLearnChargePowerAlpha", 0.2)),
            start_delay_seconds=float(_config_value(defaults, "AutoLearnChargePowerStartDelaySeconds", 30)),
            window_seconds=float(_config_value(defaults, "AutoLearnChargePowerWindowSeconds", 180)),
            max_age_seconds=float(_config_value(defaults, "AutoLearnChargePowerMaxAgeSeconds", 21600)),
        ),
        phase=AutoPhasePolicy(
            enabled=defaults.get("AutoPhaseSwitching", "1").strip().lower() in ("1", "true", "yes", "on"),
            upshift_delay_seconds=float(_config_value(defaults, "AutoPhaseUpshiftDelaySeconds", 120)),
            downshift_delay_seconds=float(_config_value(defaults, "AutoPhaseDownshiftDelaySeconds", 30)),
            upshift_headroom_watts=float(_config_value(defaults, "AutoPhaseUpshiftHeadroomWatts", 250)),
            downshift_margin_watts=float(_config_value(defaults, "AutoPhaseDownshiftMarginWatts", 150)),
            mismatch_retry_seconds=float(_config_value(defaults, "AutoPhaseMismatchRetrySeconds", 300)),
            mismatch_lockout_count=int(_config_value(defaults, "AutoPhaseMismatchLockoutCount", 3)),
            mismatch_lockout_seconds=float(_config_value(defaults, "AutoPhaseMismatchLockoutSeconds", 1800)),
            prefer_lowest_phase_when_idle=defaults.get(
                "AutoPhasePreferLowestWhenIdle",
                "1",
            ).strip().lower() in ("1", "true", "yes", "on"),
        ),
    )


def validate_auto_policy(policy: "AutoPolicy") -> "AutoPolicy":
    policy.clamp()
    return policy


def load_auto_policy_from_config(defaults: SectionProxy) -> "AutoPolicy":
    from venus_evcharger.auto.policy import AutoPolicy

    return validate_auto_policy(AutoPolicy.from_config(defaults))
