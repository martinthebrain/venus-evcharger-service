# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed component graph for focused Victron ESS balance tests."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.ports.gateway_operations import (
    EssSetpointIntent,
    GatewayOperationReceipt,
    GxRelaySetRequest,
)
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


class AcceptingGatewayOperations:
    """Semantic gateway test port that records accepted ESS operations."""

    def __init__(self) -> None:
        self.ess_operations: list[tuple[float, EssSetpointIntent]] = []

    def read_gx_relay_state(self, relay_index: int, *, max_age_seconds: float) -> int | None:
        del relay_index, max_age_seconds
        return None

    def set_gx_relay_enabled(
        self,
        request: GxRelaySetRequest,
    ) -> GatewayOperationReceipt:
        del request
        return GatewayOperationReceipt(accepted=True, command_id="relay")

    def set_ess_grid_setpoint(
        self,
        watts: float,
        *,
        intent: EssSetpointIntent,
    ) -> GatewayOperationReceipt:
        self.ess_operations.append((float(watts), intent))
        return GatewayOperationReceipt(accepted=True, command_id="ess")


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
    writer = VictronEssSetpointWriter(sources, AcceptingGatewayOperations())
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
