# SPDX-License-Identifier: GPL-3.0-or-later
"""Flat composition root for configuration and volatile service state."""

from __future__ import annotations

import configparser

from venus_evcharger.controllers.state_config import StateConfigLoader, default_state_config_path
from venus_evcharger.controllers.state_contracts import ModeNormalizer, StateControllerComponents
from venus_evcharger.controllers.state_persistence import RuntimeStatePersistence
from venus_evcharger.controllers.state_restore import RuntimeStateRestorer
from venus_evcharger.controllers.state_restore_victron_ess import VictronEssRuntimeRestorer
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.controllers.state_runtime_overrides import RuntimeOverrideStore
from venus_evcharger.controllers.state_runtime_snapshot import RuntimeStateSnapshotBuilder
from venus_evcharger.controllers.state_summary import StateSummaryBuilder
from venus_evcharger.controllers.state_validation import RuntimeConfigValidator


class ServiceStateController:
    """Stable service facade over explicitly composed state components."""

    def __init__(self, service: object, normalize_mode_func: ModeNormalizer) -> None:
        normalizer = RuntimeStateNormalizer()
        summary = StateSummaryBuilder(service)
        snapshot = RuntimeStateSnapshotBuilder(service, normalizer)
        overrides = RuntimeOverrideStore(service, normalizer)
        restorer = RuntimeStateRestorer(
            service,
            normalize_mode_func,
            normalizer,
            VictronEssRuntimeRestorer(),
        )
        persistence = RuntimeStatePersistence(service, normalizer, snapshot, restorer, summary)
        self.components = StateControllerComponents(
            config=StateConfigLoader(overrides, lambda: self.config_path()),
            validation=RuntimeConfigValidator(service),
            snapshot=snapshot,
            overrides=overrides,
            persistence=persistence,
            summary=summary,
        )

    @staticmethod
    def config_path() -> str:
        return default_state_config_path()

    @staticmethod
    def coerce_runtime_int(value: object, default: int = 0) -> int:
        return RuntimeStateNormalizer.coerce_runtime_int(value, default)

    @staticmethod
    def coerce_runtime_float(value: object, default: float = 0.0) -> float:
        return RuntimeStateNormalizer.coerce_runtime_float(value, default)

    def load_config(self) -> configparser.ConfigParser:
        return self.components.config.load()

    def validate_runtime_config(self) -> None:
        self.components.validation.validate()

    def state_summary(self) -> str:
        return self.components.summary.build()

    def current_runtime_state(self) -> dict[str, object]:
        return self.components.snapshot.build()

    def load_runtime_state(self) -> None:
        self.components.persistence.load()

    def save_runtime_state(self) -> None:
        self.components.persistence.save()

    def current_runtime_overrides(self) -> dict[str, str]:
        return self.components.overrides.current()

    def save_runtime_overrides(self) -> None:
        self.components.overrides.save()

    def flush_runtime_overrides(self, now: float | None = None) -> None:
        self.components.overrides.flush(now)
