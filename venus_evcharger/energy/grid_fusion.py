# SPDX-License-Identifier: GPL-3.0-or-later
"""Stateful primary/backup fusion for normalized grid measurements."""

from __future__ import annotations

from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig, GridFusionResult, GridMeasurement


class GridMeasurementFusion:
    """Select a primary grid measurement with safe, hysteretic fallback."""

    def __init__(self, config: GridFusionConfig) -> None:
        self.config = config
        self._active_source_id: str | None = None
        self._primary_invalid_samples = 0
        self._primary_recovery_samples = 0
        self._mismatch_samples = 0

    def resolve(
        self,
        primary: GridMeasurement,
        backup: GridMeasurement,
        now: float,
    ) -> GridFusionResult:
        primary_valid = self._measurement_usable(primary, now, self.config.primary_max_age_seconds)
        backup_valid = self._measurement_usable(backup, now, self.config.backup_max_age_seconds)
        self._record_primary(primary, primary_valid)
        selected, state = self._select_measurement(primary, backup, now, primary_valid, backup_valid)
        difference, tolerance = self._plausibility(primary, backup, primary_valid, backup_valid)
        selected, state = self._apply_disagreement_limit(
            primary,
            backup,
            selected,
            state,
            primary_valid,
            backup_valid,
        )
        return self._result(
            selected,
            state,
            primary,
            backup,
            now,
            primary_valid,
            backup_valid,
            difference,
            tolerance,
        )

    def _measurement_usable(self, measurement: GridMeasurement, now: float, max_age_seconds: float) -> bool:
        return measurement.is_usable(
            now,
            max_age_seconds=max_age_seconds,
            minimum_confidence=self.config.minimum_confidence,
            future_tolerance_seconds=self.config.future_tolerance_seconds,
        )

    def _record_primary(self, primary: GridMeasurement, primary_valid: bool) -> None:
        if primary_valid:
            self._last_primary = primary

    def _select_measurement(
        self,
        primary: GridMeasurement,
        backup: GridMeasurement,
        now: float,
        primary_valid: bool,
        backup_valid: bool,
    ) -> tuple[GridMeasurement | None, str]:
        if not self.config.enabled:
            return (backup, "backup") if backup_valid else (None, "unavailable")
        if self._active_source_id is None:
            return self._select_initial(primary, backup, primary_valid, backup_valid)
        if self._active_source_id == self.config.primary_source_id:
            return self._select_while_primary(primary, backup, now, primary_valid, backup_valid)
        return self._select_while_backup(primary, backup, primary_valid, backup_valid)

    def _select_initial(
        self,
        primary: GridMeasurement,
        backup: GridMeasurement,
        primary_valid: bool,
        backup_valid: bool,
    ) -> tuple[GridMeasurement | None, str]:
        if primary_valid:
            self._activate_primary()
            return primary, "primary"
        if backup_valid:
            self._activate_backup()
            return backup, "backup"
        return None, "unavailable"

    def _select_while_primary(
        self,
        primary: GridMeasurement,
        backup: GridMeasurement,
        now: float,
        primary_valid: bool,
        backup_valid: bool,
    ) -> tuple[GridMeasurement | None, str]:
        if primary_valid:
            self._primary_invalid_samples = 0
            return primary, "primary"
        self._primary_invalid_samples += 1
        held = self._held_primary(now)
        if self._primary_invalid_samples < self.config.failover_samples and held is not None:
            return held, "primary-held"
        if backup_valid:
            self._activate_backup()
            return backup, "backup"
        return None, "unavailable"

    def _select_while_backup(
        self,
        primary: GridMeasurement,
        backup: GridMeasurement,
        primary_valid: bool,
        backup_valid: bool,
    ) -> tuple[GridMeasurement | None, str]:
        self._record_recovery_sample(primary_valid)
        if self._primary_recovered(primary_valid):
            self._activate_primary()
            return primary, "primary"
        if backup_valid:
            return backup, self._backup_state(primary_valid)
        if primary_valid:
            return primary, "primary-emergency"
        return None, "unavailable"

    def _record_recovery_sample(self, primary_valid: bool) -> None:
        self._primary_recovery_samples = self._primary_recovery_samples + 1 if primary_valid else 0

    def _primary_recovered(self, primary_valid: bool) -> bool:
        return primary_valid and self._primary_recovery_samples >= self.config.recovery_samples

    @staticmethod
    def _backup_state(primary_valid: bool) -> str:
        return "backup-recovery" if primary_valid else "backup"

    def _activate_primary(self) -> None:
        self._active_source_id = self.config.primary_source_id
        self._primary_invalid_samples = 0
        self._primary_recovery_samples = 0

    def _activate_backup(self) -> None:
        self._active_source_id = self.config.backup_source_id
        self._primary_invalid_samples = 0
        self._primary_recovery_samples = 0

    def _held_primary(self, now: float) -> GridMeasurement | None:
        assert self._last_primary.captured_at is not None
        age_seconds = float(now) - float(self._last_primary.captured_at)
        hold_limit = self.config.primary_max_age_seconds + self.config.failover_hold_seconds
        return self._last_primary if age_seconds <= hold_limit else None

    def _plausibility(
        self,
        primary: GridMeasurement,
        backup: GridMeasurement,
        primary_valid: bool,
        backup_valid: bool,
    ) -> tuple[float | None, float | None]:
        if not self._comparable(primary_valid, backup_valid):
            self._mismatch_samples = 0
            return None, None
        assert primary.power_w is not None
        assert backup.power_w is not None
        primary_power = primary.power_w
        backup_power = backup.power_w
        difference = abs(primary_power - backup_power)
        tolerance = self._mismatch_tolerance(primary_power, backup_power)
        self._mismatch_samples = self._mismatch_samples + 1 if difference > tolerance else 0
        return difference, tolerance

    @staticmethod
    def _comparable(primary_valid: bool, backup_valid: bool) -> bool:
        return primary_valid and backup_valid

    def _mismatch_tolerance(self, primary_power: float, backup_power: float) -> float:
        scale = max(abs(float(primary_power)), abs(float(backup_power)))
        return max(self.config.mismatch_absolute_watts, self.config.mismatch_relative * scale)

    def _apply_disagreement_limit(
        self,
        primary: GridMeasurement,
        backup: GridMeasurement,
        selected: GridMeasurement | None,
        state: str,
        primary_valid: bool,
        backup_valid: bool,
    ) -> tuple[GridMeasurement | None, str]:
        if not self._persistent_disagreement(primary_valid, backup_valid):
            return selected, state
        return self._conservative_measurement(primary, backup), "disagreement"

    def _persistent_disagreement(
        self,
        primary_valid: bool,
        backup_valid: bool,
    ) -> bool:
        return primary_valid and backup_valid and self._mismatch_samples >= self.config.mismatch_samples

    @staticmethod
    def _conservative_measurement(primary: GridMeasurement, backup: GridMeasurement) -> GridMeasurement:
        assert primary.power_w is not None
        assert backup.power_w is not None
        assert primary.captured_at is not None
        assert backup.captured_at is not None
        primary_power = primary.power_w
        backup_power = backup.power_w
        primary_captured_at = primary.captured_at
        backup_captured_at = backup.captured_at
        return GridMeasurement(
            source_id="conservative",
            power_w=max(primary_power, backup_power),
            captured_at=min(primary_captured_at, backup_captured_at),
            confidence=min(float(primary.confidence), float(backup.confidence), 0.5),
        )

    def _result(
        self,
        selected: GridMeasurement | None,
        state: str,
        primary: GridMeasurement,
        backup: GridMeasurement,
        now: float,
        primary_valid: bool,
        backup_valid: bool,
        difference: float | None,
        tolerance: float | None,
    ) -> GridFusionResult:
        return GridFusionResult(
            power_w=None if selected is None else selected.power_w,
            captured_at=None if selected is None else selected.captured_at,
            selected_source_id="" if selected is None else selected.source_id,
            state=state,
            confidence=0.0 if selected is None else float(selected.confidence),
            primary_valid=primary_valid,
            backup_valid=backup_valid,
            primary_age_seconds=primary.age_seconds(now),
            backup_age_seconds=backup.age_seconds(now),
            difference_watts=difference,
            tolerance_watts=tolerance,
            primary_invalid_samples=self._primary_invalid_samples,
            primary_recovery_samples=self._primary_recovery_samples,
            mismatch_samples=self._mismatch_samples,
        )
