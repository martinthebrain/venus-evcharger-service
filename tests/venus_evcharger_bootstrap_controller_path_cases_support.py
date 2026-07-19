# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_support import (
    MagicMock,
    Path,
    ServiceBootstrapController,
    ServiceBootstrapControllerTestCase,
    SimpleNamespace,
    _FakeDbusService,
    datetime,
    patch,
    tempfile,
)
from venus_evcharger.backend.models import BackendMode, BackendRuntimeSummary
from venus_evcharger.auto.policy import AutoPolicy


def _path_auto_policy() -> AutoPolicy:
    policy = AutoPolicy()
    policy.normal_profile.start_surplus_watts = 1850.0
    policy.normal_profile.stop_surplus_watts = 1350.0
    policy.min_soc = 40.0
    policy.resume_soc = 50.0
    policy.grid_recovery_start_seconds = 14.0
    policy.stop_surplus_delay_seconds = 45.0
    policy.ewma.volatility_low_watts = 80.0
    policy.ewma.volatility_high_watts = 240.0
    policy.learn_charge_power.enabled = True
    policy.learn_charge_power.reference_power_watts = 2100.0
    policy.learn_charge_power.min_watts = 1400.0
    policy.learn_charge_power.alpha = 0.25
    policy.learn_charge_power.start_delay_seconds = 12.0
    policy.learn_charge_power.window_seconds = 180.0
    policy.learn_charge_power.max_age_seconds = 21600.0
    policy.phase.enabled = True
    policy.phase.prefer_lowest_phase_when_idle = False
    policy.phase.upshift_delay_seconds = 120.0
    policy.phase.downshift_delay_seconds = 30.0
    policy.phase.upshift_headroom_watts = 250.0
    policy.phase.downshift_margin_watts = 150.0
    policy.phase.mismatch_retry_seconds = 300.0
    policy.phase.mismatch_lockout_count = 3
    policy.phase.mismatch_lockout_seconds = 1800.0
    return policy


def _backend_runtime_summary_fixture(
    *,
    backend_mode: BackendMode = "split",
    meter_type: str | None = "template_meter",
    switch_type: str | None = "template_switch",
    charger_type: str | None = None,
    meter_config_path: Path | None = None,
    switch_config_path: Path | None = None,
    charger_config_path: Path | None = None,
) -> BackendRuntimeSummary:
    return BackendRuntimeSummary(
        backend_mode=backend_mode,
        meter_type=meter_type,
        meter_config_path=meter_config_path,
        switch_type=switch_type,
        switch_config_path=switch_config_path,
        charger_type=charger_type,
        charger_config_path=charger_config_path,
        topology_configured=True,
        primary_rpc_configured=False,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
