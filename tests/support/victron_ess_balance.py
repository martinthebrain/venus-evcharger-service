# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed component graph for focused Victron ESS balance tests."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.update.victron_ess_balance_adaptive import VictronEssAdaptiveTuner
from venus_evcharger.update.victron_ess_balance_apply import VictronEssBalanceExecutor
from venus_evcharger.update.victron_ess_balance_apply_pid import VictronEssPidController
from venus_evcharger.update.victron_ess_balance_apply_sources import VictronEssSourceResolver
from venus_evcharger.update.victron_ess_balance_apply_write import VictronEssSetpointWriter
from venus_evcharger.update.victron_ess_balance_learning_profiles import VictronEssLearningProfiles
from venus_evcharger.update.victron_ess_balance_learning_telemetry import VictronEssTelemetryRecorder
from venus_evcharger.update.victron_ess_balance_recommendation import VictronEssRecommendationEngine
from venus_evcharger.update.victron_ess_balance_recommendation_support import VictronEssRecommendationPolicy
from venus_evcharger.update.victron_ess_balance_safety import VictronEssSafetyController
from venus_evcharger.update.victron_ess_balance_safety_support import VictronEssSafetyRecovery
from venus_evcharger.update.victron_ess_balance_scoring import VictronEssTelemetryScorer


@dataclass(frozen=True)
class VictronEssComponentGraph:
    sources: VictronEssSourceResolver
    scorer: VictronEssTelemetryScorer
    profiles: VictronEssLearningProfiles
    pid: VictronEssPidController
    writer: VictronEssSetpointWriter
    recovery: VictronEssSafetyRecovery
    safety: VictronEssSafetyController
    policy: VictronEssRecommendationPolicy
    recommendation: VictronEssRecommendationEngine
    telemetry: VictronEssTelemetryRecorder
    adaptive: VictronEssAdaptiveTuner
    executor: VictronEssBalanceExecutor


def build_victron_ess_components() -> VictronEssComponentGraph:
    sources = VictronEssSourceResolver()
    scorer = VictronEssTelemetryScorer()
    profiles = VictronEssLearningProfiles(sources, scorer)
    pid = VictronEssPidController(sources)
    writer = VictronEssSetpointWriter(sources)
    recovery = VictronEssSafetyRecovery(sources, profiles)
    safety = VictronEssSafetyController(sources, pid, recovery)
    policy = VictronEssRecommendationPolicy()
    recommendation = VictronEssRecommendationEngine(policy, sources, profiles, safety, recovery)
    telemetry = VictronEssTelemetryRecorder(
        sources,
        profiles,
        safety,
        recovery,
        recommendation,
        scorer,
        pid,
    )
    adaptive = VictronEssAdaptiveTuner(sources, recommendation, recovery)
    executor = VictronEssBalanceExecutor(
        sources,
        pid,
        writer,
        profiles,
        safety,
        recovery,
        telemetry,
        recommendation,
        adaptive,
    )
    return VictronEssComponentGraph(
        sources,
        scorer,
        profiles,
        pid,
        writer,
        recovery,
        safety,
        policy,
        recommendation,
        telemetry,
        adaptive,
        executor,
    )
