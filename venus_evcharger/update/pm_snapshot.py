# SPDX-License-Identifier: GPL-3.0-or-later
"""PM snapshot normalization and cache fallback helpers."""

from __future__ import annotations

from typing import Any, ClassVar, cast

from venus_evcharger.core.contracts import (
    normalized_worker_snapshot,
    timestamp_age_within,
    timestamp_not_future,
)


class _UpdateCyclePmSnapshotMixin:
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS: ClassVar[float]

    @staticmethod
    def _worker_pm_snapshot_data(
        worker_snapshot: dict[str, Any],
        now: float,
    ) -> tuple[dict[str, Any] | None, bool, float]:
        """Return normalized worker PM data plus confirmation and timestamp."""
        normalized_snapshot = normalized_worker_snapshot(
            worker_snapshot,
            now=now,
            clamp_future_timestamps=False,
        )
        pm_status = normalized_snapshot.get("pm_status")
        if pm_status is None:
            return None, False, float(now)
        pm_status = dict(pm_status)
        pm_confirmed = bool(normalized_snapshot.get("pm_confirmed", False))
        snapshot_at = normalized_snapshot.get(
            "pm_captured_at",
            normalized_snapshot.get("captured_at", now),
        )
        return pm_status, pm_confirmed, float(now if snapshot_at is None else snapshot_at)

    @staticmethod
    def _remember_pm_snapshot(
        svc: Any,
        pm_status: dict[str, Any],
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
        svc: Any,
        now: float,
        soft_fail_seconds: float,
    ) -> dict[str, Any] | None:
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
    def _last_pm_status_marked_confirmed(svc: Any) -> bool:
        """Return whether the legacy last-PM cache is confirmed."""
        return bool(getattr(svc, "_last_pm_status_confirmed", False))

    @classmethod
    def _fresh_confirmed_pm_status(
        cls,
        pm_status: Any,
        captured_at: Any,
        now: float,
        soft_fail_seconds: float,
    ) -> dict[str, Any] | None:
        """Return one confirmed PM cache entry when it is fresh enough."""
        if not cls._cached_pm_status_usable(pm_status, captured_at, now, soft_fail_seconds):
            return None
        confirmed = dict(cast(dict[str, Any], pm_status))
        confirmed["_pm_confirmed"] = True
        return confirmed

    @classmethod
    def _cached_pm_status_usable(
        cls,
        pm_status: Any,
        captured_at: Any,
        now: float,
        soft_fail_seconds: float,
    ) -> bool:
        return isinstance(pm_status, dict) and captured_at is not None and bool(
            timestamp_age_within(
                captured_at,
                now,
                soft_fail_seconds,
                future_tolerance_seconds=cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
            )
        )

    @staticmethod
    def _direct_pm_snapshot_max_age_seconds(svc: Any) -> float:
        """Return the minimum freshness window for directly supplied worker PM snapshots."""
        candidates = [1.0]
        worker_poll_seconds = getattr(svc, "_worker_poll_interval_seconds", None)
        if worker_poll_seconds is not None:
            try:
                worker_poll_seconds = float(worker_poll_seconds)
            except (TypeError, ValueError):
                worker_poll_seconds = None
            if worker_poll_seconds is not None and worker_poll_seconds > 0:
                candidates.append(worker_poll_seconds * 2.0)
        return max(1.0, min(candidates))

    @classmethod
    def resolve_pm_status_for_update(
        cls,
        svc: Any,
        worker_snapshot: dict[str, Any],
        now: float,
    ) -> dict[str, Any] | None:
        """Return the freshest Shelly status, including short soft-fail reuse."""
        soft_fail_seconds = float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))
        pm_status, pm_confirmed, snapshot_at = cls._worker_pm_snapshot_data(worker_snapshot, now)
        if not cls._worker_pm_snapshot_usable(pm_status, pm_confirmed, snapshot_at, now):
            return cls._cached_pm_status_for_soft_fail(svc, now, soft_fail_seconds)
        pm_status = cast(dict[str, Any], pm_status)
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
        pm_status: dict[str, Any] | None,
        pm_confirmed: bool,
        snapshot_at: float,
        now: float,
    ) -> bool:
        """Return whether worker PM data may be used directly."""
        if pm_status is None or not pm_confirmed:
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
        svc: Any,
        now: float,
        snapshot_at: float,
        soft_fail_seconds: float,
    ) -> bool:
        """Return True when a PM snapshot is still usable before soft-fail fallback."""
        direct_snapshot_max_age = cls._direct_pm_snapshot_max_age_seconds(svc)
        return (float(now) - snapshot_at) <= max(soft_fail_seconds, direct_snapshot_max_age)

    @staticmethod
    def _pm_snapshot_newer_than_last(svc: Any, snapshot_at: float) -> bool:
        """Return True when a PM snapshot is at least as new as the stored one."""
        last_snapshot_at = getattr(svc, "_last_pm_status_at", None)
        return last_snapshot_at is None or snapshot_at >= float(last_snapshot_at)

    @classmethod
    def _pm_snapshot_storage_decision(
        cls,
        svc: Any,
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
