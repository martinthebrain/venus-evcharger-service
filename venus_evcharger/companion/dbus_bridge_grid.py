# SPDX-License-Identifier: GPL-3.0-or-later
"""Grid hold/smoothing helpers for the DBus companion bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping


class _EnergyCompanionDbusBridgeGridMixin:
    if TYPE_CHECKING:  # pragma: no cover
        service: Any
        _grid_hold_state: dict[str, dict[str, Any]]

        def _normalized_source_snapshots(self, snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], ...]: ...

    def _grid_connected(self, snapshot: Mapping[str, Any], now: float) -> int:
        held = self._grid_snapshot_values(snapshot, now)
        return 1 if held["connected"] else 0

    def _grid_power_w(self, snapshot: Mapping[str, Any], now: float) -> float:
        held = self._grid_snapshot_values(snapshot, now)
        return float(held["value"])

    def _grid_snapshot_values(self, snapshot: Mapping[str, Any], now: float) -> dict[str, Any]:
        raw_value, online = self._aggregate_grid_input(snapshot)
        held = self._resolved_grid_value(
            "aggregate-grid",
            raw_value=raw_value,
            online=online,
            now=now,
            hold_seconds=float(getattr(self.service, "companion_grid_hold_seconds", 0.0) or 0.0),
            smoothing_alpha=float(getattr(self.service, "companion_grid_smoothing_alpha", 1.0) or 1.0),
            smoothing_max_jump_watts=float(
                getattr(self.service, "companion_grid_smoothing_max_jump_watts", 0.0) or 0.0
            ),
        )
        return {"connected": bool(held["connected"]), "value": float(held["value"])}

    def _aggregate_grid_input(self, snapshot: Mapping[str, Any]) -> tuple[Any, bool]:
        authoritative_source_id = str(getattr(self.service, "companion_grid_authoritative_source", "")).strip()
        if authoritative_source_id:
            source = self._find_source_snapshot(snapshot, authoritative_source_id)
            if source is None:
                return None, False
            return source.get("grid_interaction_w"), bool(source.get("online", False))
        return (
            snapshot.get("battery_combined_grid_interaction_w"),
            bool(int(snapshot.get("battery_online_source_count", 0) or 0) > 0),
        )

    def _find_source_snapshot(self, snapshot: Mapping[str, Any], source_id: str) -> dict[str, Any] | None:
        for source in self._normalized_source_snapshots(snapshot):
            if str(source.get("source_id", "")).strip() == source_id:
                return source
        return None

    def _grid_source_hold_config(self) -> dict[str, float]:
        return {
            "hold_seconds": float(getattr(self.service, "companion_source_grid_hold_seconds", 0.0) or 0.0),
            "smoothing_alpha": float(getattr(self.service, "companion_source_grid_smoothing_alpha", 1.0) or 1.0),
            "smoothing_max_jump_watts": float(
                getattr(self.service, "companion_source_grid_smoothing_max_jump_watts", 0.0) or 0.0
            ),
        }

    @staticmethod
    def _grid_source_payload(held: Mapping[str, Any]) -> dict[str, Any]:
        value = float(held["value"])
        return {
            "/Connected": 1 if held["connected"] else 0,
            "/Ac/Power": value,
            "/Ac/L1/Power": value,
            "/Ac/L2/Power": 0.0,
            "/Ac/L3/Power": 0.0,
        }

    def _grid_source_values(self, source: Mapping[str, Any], now: float) -> dict[str, Any]:
        source_id = str(source.get("source_id", "")).strip() or "source"
        hold_config = self._grid_source_hold_config()
        held = self._resolved_grid_value(
            f"source-grid:{source_id}",
            raw_value=source.get("grid_interaction_w"),
            online=bool(source.get("online", False)),
            now=now,
            hold_seconds=hold_config["hold_seconds"],
            smoothing_alpha=hold_config["smoothing_alpha"],
            smoothing_max_jump_watts=hold_config["smoothing_max_jump_watts"],
        )
        return self._grid_source_payload(held)

    @staticmethod
    def _grid_numeric_value(raw_value: Any) -> float | None:
        return float(raw_value) if isinstance(raw_value, (int, float)) else None

    @staticmethod
    def _grid_normalized_alpha(smoothing_alpha: float) -> float:
        return min(1.0, max(0.0, float(smoothing_alpha)))

    @staticmethod
    def _grid_smoothed_value(
        numeric_value: float,
        previous_value: Any,
        normalized_alpha: float,
        smoothing_max_jump_watts: float,
    ) -> float:
        if not isinstance(previous_value, (int, float)) or not 0.0 < normalized_alpha < 1.0:
            return float(numeric_value)
        delta_watts = abs(float(numeric_value) - float(previous_value))
        if float(smoothing_max_jump_watts) > 0.0 and delta_watts > float(smoothing_max_jump_watts):
            return float(numeric_value)
        return float((normalized_alpha * numeric_value) + ((1.0 - normalized_alpha) * float(previous_value)))

    @staticmethod
    def _grid_resolved_payload(value: float, connected: bool, last_good_at: float | None) -> dict[str, Any]:
        return {
            "value": float(value),
            "connected": bool(connected),
            "last_good_at": last_good_at,
        }

    @staticmethod
    def _grid_within_hold_window(cached: Mapping[str, Any], now: float, hold_seconds: float) -> bool:
        last_good_at = cached.get("last_good_at")
        return bool(
            isinstance(last_good_at, (int, float))
            and hold_seconds > 0.0
            and float(now) - float(last_good_at) <= float(hold_seconds)
        )

    def _resolved_grid_value(
        self,
        state_key: str,
        *,
        raw_value: Any,
        online: bool,
        now: float,
        hold_seconds: float,
        smoothing_alpha: float,
        smoothing_max_jump_watts: float,
    ) -> dict[str, Any]:
        cached = self._grid_hold_state.get(state_key, {})
        numeric_value = self._grid_numeric_value(raw_value)
        normalized_alpha = self._grid_normalized_alpha(smoothing_alpha)
        if numeric_value is not None:
            smoothed_value = self._grid_smoothed_value(
                numeric_value,
                cached.get("value"),
                normalized_alpha,
                smoothing_max_jump_watts,
            )
            resolved = self._grid_resolved_payload(smoothed_value, bool(online), float(now))
            self._grid_hold_state[state_key] = resolved
            return resolved
        held_last_good_at = cached.get("last_good_at")
        held_value = cached.get("value")
        if (
            isinstance(held_last_good_at, (int, float))
            and isinstance(held_value, (int, float))
            and self._grid_within_hold_window(cached, now, hold_seconds)
        ):
            return self._grid_resolved_payload(float(held_value), True, held_last_good_at)
        self._grid_hold_state.pop(state_key, None)
        return self._grid_resolved_payload(0.0, False, None)
