# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.venus_evcharger_update_cycle_controller_support import initialize_victron_test_service
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
from tests.support.victron_ess_balance import VictronEssComponentGraph, build_victron_ess_components
from venus_evcharger.update.victron_ess_balance_apply import VictronEssBalanceExecutor


def _components() -> VictronEssComponentGraph:
    return build_victron_ess_components()


def _controller() -> VictronEssBalanceExecutor:
    return _components().executor


class _TelemetryHarness:
    def __init__(self) -> None:
        components = _components()
        self.telemetry = components.telemetry
        self.pid = components.pid
        self.scorer = components.scorer
        self.delay_updates: list[tuple[str, float]] = []
        self.gain_updates: list[tuple[str, float]] = []
        self.counter_updates: list[tuple[str, str]] = []
        self.cooldowns: list[tuple[float, str]] = []
        self.metrics_calls = 0
        self.refreshed_profiles: list[str] = []
        components.profiles._victron_ess_balance_update_profile_delay = MagicMock(
            side_effect=lambda _svc, key, sample: self.delay_updates.append((key, sample))
        )
        components.profiles._victron_ess_balance_update_profile_gain = MagicMock(
            side_effect=lambda _svc, key, sample: self.gain_updates.append((key, sample))
        )
        components.profiles._victron_ess_balance_increment_profile_counter = MagicMock(
            side_effect=lambda _svc, key, field: self.counter_updates.append((key, field))
        )
        components.recovery._enter_victron_ess_balance_overshoot_cooldown = MagicMock(
            side_effect=lambda _svc, now, reason: self.cooldowns.append((now, reason))
        )
        components.safety._victron_ess_balance_telemetry_is_clean = MagicMock(return_value=(True, "clean"))
        components.sources._victron_ess_balance_ev_power_w = MagicMock(return_value=0.0)
        components.profiles._victron_ess_balance_refresh_profile_stability = MagicMock(
            side_effect=lambda _svc, key: self.refreshed_profiles.append(key)
        )

        def populate_metrics(_svc: object, metrics: dict[str, object]) -> None:
            self.metrics_calls += 1
            metrics["telemetry_metrics_populated"] = True

        components.recommendation._populate_victron_ess_balance_telemetry_metrics = MagicMock(
            side_effect=populate_metrics
        )


__all__ = [name for name in globals() if not name.startswith("__")]
