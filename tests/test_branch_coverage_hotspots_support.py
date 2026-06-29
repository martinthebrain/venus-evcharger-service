# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.venus_evcharger_update_cycle_controller_support import UpdateCycleController, _phase_values
from venus_evcharger.bootstrap.wizard_energy import (
    _config_auto_energy_sources_value,
    _structured_energy_source_line,
    _structured_energy_source_value,
    build_suggested_energy_merge,
    bundle_block_label,
    bundle_labels,
    bundle_target_names,
    bundle_source_id,
    energy_source_capacity_follow_up,
    energy_source_merge_lines,
    existing_auto_energy_assignments,
    existing_auto_energy_source_ids,
    existing_source_ids_from_assignments,
    huawei_bundle_files,
    manual_review_union,
    merge_energy_source_ids,
    merged_recommendation_prefixes,
    normalized_recommendation_prefixes,
    optional_capacity_wh,
    structured_energy_source_from_block,
    suggested_energy_assignments,
    suggested_energy_merge_lines,
    suggested_energy_sources_with_capacity,
    suggested_energy_sources_with_capacity_overrides,
    validate_unique_suggested_energy_sources,
)
from venus_evcharger.bootstrap.wizard_render import (
    _remaining_default_assignment_lines,
    live_check_rendered_setup,
    live_connectivity_payload,
    upsert_default_assignments,
)
from venus_evcharger.update.victron_ess_balance_learning_profiles import (
    _victron_ess_balance_profile_identity,
)
from venus_evcharger.update.victron_ess_balance_learning_telemetry import (
    _UpdateCycleVictronEssBalanceLearningTelemetry,
)


def _controller() -> UpdateCycleController:
    return UpdateCycleController(SimpleNamespace(), _phase_values, lambda _reason: 0)


class _TelemetryHarness(_UpdateCycleVictronEssBalanceLearningTelemetry):
    @staticmethod
    def _optional_float(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return float(value)

    _ewma_learned_value = staticmethod(_UpdateCycleVictronEssBalanceLearningTelemetry._ewma_learned_value)

    def __init__(self) -> None:
        self.delay_updates: list[tuple[str, float]] = []
        self.gain_updates: list[tuple[str, float]] = []
        self.counter_updates: list[tuple[str, str]] = []
        self.cooldowns: list[tuple[float, str]] = []
        self.metrics_calls = 0
        self.refreshed_profiles: list[str] = []

    def _victron_ess_balance_update_profile_delay(self, svc: object, profile_key: str, sample: float) -> None:
        self.delay_updates.append((profile_key, sample))

    def _victron_ess_balance_update_profile_gain(self, svc: object, profile_key: str, sample: float) -> None:
        self.gain_updates.append((profile_key, sample))

    def _victron_ess_balance_increment_profile_counter(self, svc: object, profile_key: str, field: str) -> None:
        self.counter_updates.append((profile_key, field))

    def _enter_victron_ess_balance_overshoot_cooldown(self, svc: object, now: float, reason: str) -> None:
        self.cooldowns.append((now, reason))

    def _victron_ess_balance_profile_sample_count(self, profile: dict[str, object]) -> int:
        return max(0, int(profile.get("delay_samples", 0) or 0))

    def _victron_ess_balance_telemetry_is_clean(
        self,
        svc: object,
        cluster: dict[str, object],
        source_error_w: float,
    ) -> tuple[bool, str]:
        return True, "clean"

    def _victron_ess_balance_ev_power_w(self, svc: object) -> float:
        return 0.0

    def _victron_ess_balance_refresh_profile_stability(self, svc: object, profile_key: str) -> None:
        self.refreshed_profiles.append(profile_key)

    def _populate_victron_ess_balance_telemetry_metrics(self, svc: object, metrics: dict[str, object]) -> None:
        self.metrics_calls += 1
        metrics["telemetry_metrics_populated"] = True


__all__ = [name for name in globals() if not name.startswith("__")]
