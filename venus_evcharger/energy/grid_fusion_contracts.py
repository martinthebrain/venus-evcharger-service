# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable contracts for normalized primary/backup grid measurements."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class GridFusionConfig:
    """Define freshness, plausibility, and source-switching policy."""

    enabled: bool = False
    primary_source_id: str = ""
    backup_source_id: str = "victron"
    primary_max_age_seconds: float = 15.0
    backup_max_age_seconds: float = 6.0
    minimum_confidence: float = 0.5
    failover_samples: int = 3
    recovery_samples: int = 15
    failover_hold_seconds: float = 6.0
    mismatch_absolute_watts: float = 300.0
    mismatch_relative: float = 0.15
    mismatch_samples: int = 3
    future_tolerance_seconds: float = 1.0

    def __post_init__(self) -> None:
        _validate_grid_fusion_config(self)


def _validate_grid_fusion_config(config: GridFusionConfig) -> None:
    _require(not config.enabled or bool(config.primary_source_id.strip()), "Grid fusion requires a primary source id")
    _require(bool(config.backup_source_id.strip()), "Grid fusion requires a backup source id")
    _validate_sample_thresholds(config)
    _validate_freshness_policy(config)
    _validate_mismatch_policy(config)


def _validate_sample_thresholds(config: GridFusionConfig) -> None:
    thresholds = (config.failover_samples, config.recovery_samples, config.mismatch_samples)
    _require(all(value >= 1 for value in thresholds), "Grid fusion sample thresholds must be positive")


def _validate_freshness_policy(config: GridFusionConfig) -> None:
    ages = (config.primary_max_age_seconds, config.backup_max_age_seconds)
    tolerances = (config.failover_hold_seconds, config.future_tolerance_seconds)
    _require(all(value >= 0.0 for value in ages), "Grid fusion freshness limits must be non-negative")
    _require(0.0 <= config.minimum_confidence <= 1.0, "Grid fusion minimum confidence must be between zero and one")
    _require(all(value >= 0.0 for value in tolerances), "Grid fusion time tolerances must be non-negative")


def _validate_mismatch_policy(config: GridFusionConfig) -> None:
    tolerances = (config.mismatch_absolute_watts, config.mismatch_relative)
    _require(all(value >= 0.0 for value in tolerances), "Grid fusion mismatch tolerances must be non-negative")


@dataclass(frozen=True)
class GridMeasurement:
    """One normalized grid measurement where import is positive."""

    source_id: str
    power_w: float | None
    captured_at: float | None
    online: bool = True
    confidence: float = 1.0

    def is_usable(
        self,
        now: float,
        *,
        max_age_seconds: float,
        minimum_confidence: float,
        future_tolerance_seconds: float,
    ) -> bool:
        if not self._has_online_value() or not self._values_are_finite():
            return False
        assert self.captured_at is not None
        age_seconds = float(now) - float(self.captured_at)
        freshness_ok = -float(future_tolerance_seconds) <= age_seconds <= float(max_age_seconds)
        return freshness_ok and float(self.confidence) >= float(minimum_confidence)

    def _has_online_value(self) -> bool:
        return self.online and self.power_w is not None and self.captured_at is not None

    def _values_are_finite(self) -> bool:
        values = (self.power_w, self.captured_at, self.confidence)
        return all(value is not None and math.isfinite(value) for value in values)

    def age_seconds(self, now: float) -> float | None:
        if self.captured_at is None or not math.isfinite(self.captured_at):
            return None
        return max(0.0, float(now) - float(self.captured_at))


@dataclass(frozen=True)
class GridFusionResult:
    """Canonical grid value plus explainable source-selection diagnostics."""

    power_w: float | None
    captured_at: float | None
    selected_source_id: str
    state: str
    confidence: float
    primary_valid: bool
    backup_valid: bool
    primary_age_seconds: float | None
    backup_age_seconds: float | None
    difference_watts: float | None
    tolerance_watts: float | None
    primary_invalid_samples: int
    primary_recovery_samples: int
    mismatch_samples: int
