# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable contracts for normalized primary/backup grid measurements."""

from __future__ import annotations

import math
from dataclasses import dataclass

from venus_evcharger.energy.timestamped_measurement import TimestampedMeasurement


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


@dataclass(frozen=True, slots=True)
class GridMeasurement:
    """One normalized grid measurement with epoch and monotonic timestamps."""

    source_id: str
    measurement: TimestampedMeasurement[float]
    online: bool = True
    confidence: float = 1.0

    @property
    def power_w(self) -> float | None:
        return self.measurement.value

    @property
    def captured_at(self) -> float | None:
        return self.measurement.captured_at

    @property
    def observed_monotonic(self) -> float | None:
        return self.measurement.observed_monotonic

    def is_usable(
        self,
        monotonic_at: float,
        *,
        max_age_seconds: float,
        minimum_confidence: float,
        future_tolerance_seconds: float,
    ) -> bool:
        if not self._has_online_value() or not self._values_are_finite():
            return False
        return self.measurement.fresh(
            monotonic_at,
            max_age_seconds=max_age_seconds,
            future_tolerance_seconds=future_tolerance_seconds,
        ) and float(self.confidence) >= float(minimum_confidence)

    def _has_online_value(self) -> bool:
        return (
            self.online
            and self.measurement.available
        )

    def _values_are_finite(self) -> bool:
        values = (
            self.power_w,
            self.confidence,
        )
        return all(value is not None and math.isfinite(value) for value in values)

    def age_seconds(self, monotonic_at: float) -> float | None:
        return self.measurement.age_seconds(monotonic_at)


@dataclass(frozen=True, slots=True)
class GridFusionResult:
    """Canonical grid value plus explainable source-selection diagnostics."""

    measurement: TimestampedMeasurement[float]
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

    @property
    def power_w(self) -> float | None:
        return self.measurement.value

    @property
    def captured_at(self) -> float | None:
        return self.measurement.captured_at

    @property
    def observed_monotonic(self) -> float | None:
        return self.measurement.observed_monotonic
