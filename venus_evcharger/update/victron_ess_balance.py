# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental Victron ESS balance-bias helpers for the update cycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from venus_evcharger.ports.gateway_operations import GatewayOperationsPort

from .victron_ess_balance_adaptive import VictronEssAdaptiveTuner
from .victron_ess_balance_apply import VictronEssBalanceExecutor
from .victron_ess_balance_apply_pid import VictronEssPidController
from .victron_ess_balance_apply_sources import VictronEssSourceResolver
from .victron_ess_balance_apply_write import VictronEssSetpointWriter
from .victron_ess_balance_learning_profiles import VictronEssLearningProfiles
from .victron_ess_balance_learning_telemetry import VictronEssTelemetryRecorder
from .victron_ess_balance_recommendation import VictronEssRecommendationEngine
from .victron_ess_balance_recommendation_support import VictronEssRecommendationPolicy
from .victron_ess_balance_safety import VictronEssSafetyController
from .victron_ess_balance_safety_support import VictronEssSafetyRecovery
from .victron_ess_balance_scoring import VictronEssTelemetryScorer


@dataclass(frozen=True, slots=True)
class VictronEssBalanceComponents:
    """Named ownership graph for the optional ESS balance controller."""

    sources: VictronEssSourceResolver
    scorer: VictronEssTelemetryScorer
    profiles: VictronEssLearningProfiles
    pid: VictronEssPidController
    writer: VictronEssSetpointWriter
    recovery: VictronEssSafetyRecovery
    safety: VictronEssSafetyController
    recommendation_policy: VictronEssRecommendationPolicy
    recommendation: VictronEssRecommendationEngine
    telemetry: VictronEssTelemetryRecorder
    adaptive: VictronEssAdaptiveTuner
    executor: VictronEssBalanceExecutor


class VictronEssBalanceController:
    """Apply and learn the optional Victron ESS balance bias."""

    def __init__(self, gateway_operations: GatewayOperationsPort) -> None:
        sources = VictronEssSourceResolver()
        scorer = VictronEssTelemetryScorer()
        profiles = VictronEssLearningProfiles(sources, scorer)
        pid = VictronEssPidController(sources)
        writer = VictronEssSetpointWriter(sources, gateway_operations)
        recovery = VictronEssSafetyRecovery(sources, profiles)
        safety = VictronEssSafetyController(sources, pid, recovery)
        recommendation_policy = VictronEssRecommendationPolicy()
        recommendation = VictronEssRecommendationEngine(
            recommendation_policy,
            sources,
            profiles,
            safety,
            recovery,
        )
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
        self.components = VictronEssBalanceComponents(
            sources=sources,
            scorer=scorer,
            profiles=profiles,
            pid=pid,
            writer=writer,
            recovery=recovery,
            safety=safety,
            recommendation_policy=recommendation_policy,
            recommendation=recommendation,
            telemetry=telemetry,
            adaptive=adaptive,
            executor=executor,
        )

    def apply_victron_ess_balance_bias(self, svc: Any, now: float, auto_mode_active: bool) -> None:
        self.components.executor.apply_victron_ess_balance_bias(svc, now, auto_mode_active)

    def victron_ess_balance_learning_state_payload(self, svc: Any) -> dict[str, Any]:
        return self.components.profiles.victron_ess_balance_learning_state_payload(svc)

    def victron_ess_balance_adaptive_tuning_payload(self, svc: Any) -> dict[str, Any]:
        return self.components.profiles.victron_ess_balance_adaptive_tuning_payload(svc)


__all__ = ["VictronEssBalanceComponents", "VictronEssBalanceController"]
