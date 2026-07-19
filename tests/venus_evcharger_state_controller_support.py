# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field

from venus_evcharger.auto.policy import (
    AutoLearnChargePowerPolicy,
    AutoPhasePolicy,
    AutoPolicy,
    AutoStopEwmaPolicy,
    AutoThresholdProfile,
)
from venus_evcharger.controllers.state_restore import RuntimeStateRestorer
from venus_evcharger.controllers.state_restore_victron_ess import VictronEssRuntimeRestorer
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer

STATE_SUMMARY_TIME = "venus_evcharger.controllers.state_summary.time.time"
STATE_RUNTIME_PARSER_READ = "venus_evcharger.controllers.state_runtime_overrides.CasePreservingConfigParser.read"
STATE_RUNTIME_WRITE = "venus_evcharger.controllers.state_runtime_overrides.write_text_atomically"
STATE_RUNTIME_LOG_WARNING = "venus_evcharger.controllers.state_json.logging.warning"
STATE_RESTORE_WRITE = "venus_evcharger.controllers.state_persistence.write_text_atomically"
STATE_RESTORE_LOG_WARNING = "venus_evcharger.controllers.state_persistence.logging.warning"


def _empty_string_mapping() -> dict[str, str]:
    return {}


def runtime_override_policy() -> AutoPolicy:
    return AutoPolicy(
        normal_profile=AutoThresholdProfile(1850.0, 1350.0),
        min_soc=40.0,
        resume_soc=50.0,
        grid_recovery_start_seconds=14.0,
        stop_surplus_delay_seconds=45.0,
        ewma=AutoStopEwmaPolicy(
            volatility_low_watts=80.0,
            volatility_high_watts=240.0,
        ),
        learn_charge_power=AutoLearnChargePowerPolicy(
            enabled=False,
            reference_power_watts=2100.0,
            min_watts=1400.0,
            alpha=0.25,
            start_delay_seconds=12.0,
            window_seconds=180.0,
            max_age_seconds=21600.0,
        ),
        phase=AutoPhasePolicy(
            enabled=True,
            prefer_lowest_phase_when_idle=False,
            upshift_delay_seconds=120.0,
            downshift_delay_seconds=30.0,
            upshift_headroom_watts=250.0,
            downshift_margin_watts=150.0,
            mismatch_retry_seconds=300.0,
            mismatch_lockout_count=3,
            mismatch_lockout_seconds=1800.0,
        ),
    )


@dataclass
class RuntimeOverrideServiceFixture:
    runtime_overrides_path: str
    runtime_overrides_write_min_interval_seconds: float = 0.0
    time_now: Callable[[], float] = field(default=lambda: 0.0)
    virtual_mode: int = 1
    virtual_autostart: int = 0
    virtual_set_current: float = 13.5
    min_current: float = 6.0
    max_current: float = 16.0
    requested_phase_selection: str = "P1"
    auto_start_delay_seconds: float = 10.0
    auto_stop_delay_seconds: float = 30.0
    auto_scheduled_enabled_days: str = "Mon,Tue,Wed,Thu,Fri"
    auto_scheduled_night_start_delay_seconds: float = 3600.0
    auto_scheduled_latest_end_time: str = "06:30"
    auto_scheduled_night_current_amps: float = 13.0
    auto_dbus_backoff_base_seconds: float = 5.0
    auto_dbus_backoff_max_seconds: float = 60.0
    auto_policy: AutoPolicy = field(default_factory=runtime_override_policy)
    _runtime_overrides_serialized: str | None = None
    _runtime_overrides_last_saved_at: float | None = None
    _runtime_overrides_pending_serialized: str | None = None
    _runtime_overrides_pending_values: dict[str, str] | None = None
    _runtime_overrides_pending_text: str | None = None
    _runtime_overrides_pending_due_at: float | None = None
    _runtime_overrides_active: bool = False
    _runtime_overrides_values: dict[str, str] = field(default_factory=_empty_string_mapping)



class ServiceStateControllerTestBase(unittest.TestCase):
    @staticmethod
    def _normalize_mode(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float, str)):
            return int(value)
        return 0

    @classmethod
    def runtime_restorer(cls, service: object) -> RuntimeStateRestorer:
        return RuntimeStateRestorer(
            service,
            cls._normalize_mode,
            RuntimeStateNormalizer(),
            VictronEssRuntimeRestorer(),
        )


__all__ = (
    "RuntimeOverrideServiceFixture",
    "ServiceStateControllerTestBase",
    "STATE_RESTORE_LOG_WARNING",
    "STATE_RESTORE_WRITE",
    "STATE_RUNTIME_LOG_WARNING",
    "STATE_RUNTIME_PARSER_READ",
    "STATE_RUNTIME_WRITE",
    "STATE_SUMMARY_TIME",
    "runtime_override_policy",
)
