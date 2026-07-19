# SPDX-License-Identifier: GPL-3.0-or-later
"""PM snapshot normalization and cache fallback helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol, TypeGuard

from venus_evcharger.core.contracts import (
    finite_float_or_none,
    normalized_worker_snapshot,
    timestamp_age_within,
    timestamp_not_future,
)


class PmSnapshotService(Protocol):
    auto_shelly_soft_fail_seconds: float
    _worker_poll_interval_seconds: float
    _last_pm_status: dict[str, object] | None
    _last_pm_status_at: float | None
    _last_pm_status_confirmed: bool
    _last_confirmed_pm_status: dict[str, object] | None
    _last_confirmed_pm_status_at: float | None


def _is_string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


class PmSnapshotResolver:
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS: ClassVar[float] = 1.0
    CLAMP_WORKER_PM_FUTURE_TIMESTAMPS: ClassVar[bool] = False

    @classmethod
    def _worker_pm_snapshot_data(
        cls,
        worker_snapshot: dict[str, object],
        now: float,
    ) -> tuple[dict[str, object] | None, bool, float]:
        """Return normalized worker PM data plus confirmation and timestamp."""
        normalized_snapshot = normalized_worker_snapshot(
            worker_snapshot,
            now=now,
            clamp_future_timestamps=cls.CLAMP_WORKER_PM_FUTURE_TIMESTAMPS,
        )
        pm_status = cls._worker_pm_status_payload(normalized_snapshot)
        if pm_status is None:
            return None, False, float(now)
        return (
            pm_status,
            cls._worker_pm_confirmed(normalized_snapshot),
            cls._worker_pm_snapshot_timestamp(normalized_snapshot),
        )

    @staticmethod
    def _worker_pm_status_payload(normalized_snapshot: Mapping[str, object]) -> dict[str, object] | None:
        """Return a defensive copy of normalized PM payload data."""
        pm_status = normalized_snapshot["pm_status"]
        return dict(pm_status) if _is_string_object_dict(pm_status) else None

    @staticmethod
    def _worker_pm_confirmed(normalized_snapshot: Mapping[str, object]) -> bool:
        """Return whether the normalized PM payload is confirmed by the helper."""
        return bool(normalized_snapshot["pm_confirmed"])

    @staticmethod
    def _worker_pm_snapshot_timestamp(normalized_snapshot: Mapping[str, object]) -> float:
        """Return the PM sample timestamp from normalized worker data."""
        snapshot_at = finite_float_or_none(normalized_snapshot["pm_captured_at"])
        if snapshot_at is None:
            snapshot_at = finite_float_or_none(normalized_snapshot["captured_at"])
        return 0.0 if snapshot_at is None else snapshot_at

    @staticmethod
    def _remember_pm_snapshot(
        svc: PmSnapshotService,
        pm_status: dict[str, object],
        snapshot_at: float,
        pm_confirmed: bool,
    ) -> None:
        """Persist the freshest known PM status for short read-soft-fail reuse."""
        remembered = dict(pm_status)
        remembered["_pm_confirmed"] = pm_confirmed
        svc._last_pm_status = remembered
        svc._last_pm_status_at = snapshot_at
        svc._last_pm_status_confirmed = pm_confirmed
        if pm_confirmed:
            svc._last_confirmed_pm_status = dict(remembered)
            svc._last_confirmed_pm_status_at = snapshot_at

    @classmethod
    def _cached_pm_status_for_soft_fail(
        cls,
        svc: PmSnapshotService,
        now: float,
        soft_fail_seconds: float,
    ) -> dict[str, object] | None:
        """Return the last confirmed PM status when it is still inside soft-fail budget."""
        confirmed = cls._fresh_confirmed_pm_status(
            getattr(svc, "_last_confirmed_pm_status", None),
            getattr(svc, "_last_confirmed_pm_status_at", None),
            now,
            soft_fail_seconds,
        )
        if confirmed is not None:
            return confirmed
        if not cls._last_pm_status_marked_confirmed(svc):
            return None
        return cls._fresh_confirmed_pm_status(
            getattr(svc, "_last_pm_status", None),
            getattr(svc, "_last_pm_status_at", None),
            now,
            soft_fail_seconds,
        )

    @staticmethod
    def _last_pm_status_marked_confirmed(svc: PmSnapshotService) -> bool:
        """Return whether the legacy last-PM cache is confirmed."""
        if not hasattr(svc, "_last_pm_status_confirmed"):
            return False
        return bool(getattr(svc, "_last_pm_status_confirmed"))

    @classmethod
    def _fresh_confirmed_pm_status(
        cls,
        pm_status: object,
        captured_at: object,
        now: float,
        soft_fail_seconds: float,
    ) -> dict[str, object] | None:
        """Return one confirmed PM cache entry when it is fresh enough."""
        if not _is_string_object_dict(pm_status) or captured_at is None:
            return None
        if not timestamp_age_within(
            captured_at,
            now,
            soft_fail_seconds,
            future_tolerance_seconds=cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
        ):
            return None
        confirmed = dict(pm_status)
        confirmed["_pm_confirmed"] = True
        return confirmed

    @staticmethod
    def _direct_pm_snapshot_max_age_seconds(svc: PmSnapshotService) -> float:
        """Return the freshness window for directly supplied worker PM snapshots."""
        worker_poll_seconds = finite_float_or_none(getattr(svc, "_worker_poll_interval_seconds", None))
        if worker_poll_seconds is None:
            return 1.0
        return max(1.0, worker_poll_seconds * 2.0)

    @classmethod
    def resolve_pm_status_for_update(
        cls,
        svc: PmSnapshotService,
        worker_snapshot: dict[str, object],
        now: float,
    ) -> dict[str, object] | None:
        """Return the freshest Shelly status, including short soft-fail reuse."""
        soft_fail_seconds = float(svc.auto_shelly_soft_fail_seconds)
        pm_status, pm_confirmed, snapshot_at = cls._worker_pm_snapshot_data(worker_snapshot, now)
        if pm_status is None or not cls._worker_pm_snapshot_usable(pm_confirmed, snapshot_at, now):
            return cls._cached_pm_status_for_soft_fail(svc, now, soft_fail_seconds)
        pm_status["_pm_confirmed"] = True
        should_remember, within_soft_fail = cls._pm_snapshot_storage_decision(
            svc,
            now,
            snapshot_at,
            soft_fail_seconds,
        )
        if should_remember:
            cls._remember_pm_snapshot(svc, pm_status, snapshot_at, pm_confirmed)
        if within_soft_fail:
            return pm_status
        return cls._cached_pm_status_for_soft_fail(svc, now, soft_fail_seconds)

    @classmethod
    def _worker_pm_snapshot_usable(
        cls,
        pm_confirmed: bool,
        snapshot_at: float,
        now: float,
    ) -> bool:
        """Return whether worker PM data may be used directly."""
        if not pm_confirmed:
            return False
        return not cls._pm_snapshot_falls_back_to_cache(snapshot_at, now)

    @classmethod
    def _pm_snapshot_from_future(cls, snapshot_at: float, now: float) -> bool:
        """Return True when a worker PM snapshot timestamp lies implausibly in the future."""
        return not timestamp_not_future(
            snapshot_at,
            now,
            cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
        )

    @classmethod
    def _pm_snapshot_falls_back_to_cache(cls, snapshot_at: float, now: float) -> bool:
        """Return True when a PM snapshot must immediately fall back to cached state."""
        return cls._pm_snapshot_from_future(snapshot_at, now)

    @classmethod
    def _pm_snapshot_within_soft_fail_budget(
        cls,
        svc: PmSnapshotService,
        now: float,
        snapshot_at: float,
        soft_fail_seconds: float,
    ) -> bool:
        """Return True when a PM snapshot is still usable before soft-fail fallback."""
        direct_snapshot_max_age = cls._direct_pm_snapshot_max_age_seconds(svc)
        return (float(now) - snapshot_at) <= max(soft_fail_seconds, direct_snapshot_max_age)

    @staticmethod
    def _remembered_pm_snapshot_timestamp(svc: PmSnapshotService) -> float | None:
        """Return the newest known PM timestamp across direct and confirmed caches."""
        candidates = [
            finite_float_or_none(getattr(svc, "_last_pm_status_at", None)),
            finite_float_or_none(getattr(svc, "_last_confirmed_pm_status_at", None)),
        ]
        fresh_candidates = [candidate for candidate in candidates if candidate is not None]
        return max(fresh_candidates) if fresh_candidates else None

    @classmethod
    def _pm_snapshot_newer_than_last(cls, svc: PmSnapshotService, snapshot_at: float) -> bool:
        """Return True when a PM snapshot is at least as new as the stored one."""
        last_snapshot_at = cls._remembered_pm_snapshot_timestamp(svc)
        return last_snapshot_at is None or snapshot_at >= float(last_snapshot_at)

    @classmethod
    def _pm_snapshot_storage_decision(
        cls,
        svc: PmSnapshotService,
        now: float,
        snapshot_at: float,
        soft_fail_seconds: float,
    ) -> tuple[bool, bool]:
        """Return whether to remember a PM snapshot and whether it stays directly usable."""
        within_soft_fail = cls._pm_snapshot_within_soft_fail_budget(
            svc,
            now,
            snapshot_at,
            soft_fail_seconds,
        )
        should_remember = within_soft_fail or cls._pm_snapshot_newer_than_last(svc, snapshot_at)
        return should_remember, within_soft_fail


__all__ = ["PmSnapshotResolver", "PmSnapshotService"]
