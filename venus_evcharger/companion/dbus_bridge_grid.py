# SPDX-License-Identifier: GPL-3.0-or-later
"""Grid hold/smoothing helpers for the DBus companion bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping


class _EnergyCompanionDbusBridgeGrid:
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
        hold_config = self._grid_hold_config()
        held = self._resolved_grid_value(
            "aggregate-grid",
            raw_value=raw_value,
            online=online,
            now=now,
            hold_seconds=hold_config["hold_seconds"],
            smoothing_alpha=hold_config["smoothing_alpha"],
            smoothing_max_jump_watts=hold_config["smoothing_max_jump_watts"],
        )
        return {"connected": bool(held["connected"]), "value": float(held["value"])}

    def _aggregate_grid_input(self, snapshot: Mapping[str, Any]) -> tuple[Any, bool]:
        if bool(snapshot.get("grid_fusion_enabled", False)):
            fused_value = snapshot.get("grid_power")
            return fused_value, self._grid_numeric_value(fused_value) is not None
        authoritative_source_id = self._string_value(getattr(self.service, "companion_grid_authoritative_source", None))
        if authoritative_source_id:
            source = self._find_source_snapshot(snapshot, authoritative_source_id)
            if source is None:
                return None, False
            return source.get("grid_interaction_w"), self._online_value(source)
        return (
            snapshot.get("battery_combined_grid_interaction_w"),
            self._online_count_positive(snapshot),
        )

    def _find_source_snapshot(self, snapshot: Mapping[str, Any], source_id: str) -> dict[str, Any] | None:
        for source in self._normalized_source_snapshots(snapshot):
            if self._source_id_value(source) == source_id:
                return source
        return None

    def _grid_hold_config(self) -> dict[str, float]:
        return {
            "hold_seconds": self._float_service_attr("companion_grid_hold_seconds", 0.0),
            "smoothing_alpha": self._float_service_attr("companion_grid_smoothing_alpha", 1.0),
            "smoothing_max_jump_watts": self._float_service_attr("companion_grid_smoothing_max_jump_watts", 0.0),
        }

    def _grid_source_hold_config(self) -> dict[str, float]:
        return {
            "hold_seconds": self._float_service_attr("companion_source_grid_hold_seconds", 0.0),
            "smoothing_alpha": self._float_service_attr("companion_source_grid_smoothing_alpha", 1.0),
            "smoothing_max_jump_watts": self._float_service_attr(
                "companion_source_grid_smoothing_max_jump_watts",
                0.0,
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
        source_id = self._source_id_value(source) or "source"
        hold_config = self._grid_source_hold_config()
        held = self._resolved_grid_value(
            f"source-grid:{source_id}",
            raw_value=source.get("grid_interaction_w"),
            online=self._online_value(source),
            now=now,
            hold_seconds=hold_config["hold_seconds"],
            smoothing_alpha=hold_config["smoothing_alpha"],
            smoothing_max_jump_watts=hold_config["smoothing_max_jump_watts"],
        )
        return self._grid_source_payload(held)

    def _float_service_attr(self, name: str, fallback: float) -> float:
        raw_value = getattr(self.service, name, None)
        if raw_value is None:
            return float(fallback)
        numeric_value = float(raw_value)
        return numeric_value if numeric_value != 0.0 else float(fallback)

    @staticmethod
    def _string_value(raw_value: Any) -> str:
        return "" if raw_value is None else str(raw_value).strip()

    @classmethod
    def _source_id_value(cls, source: Mapping[str, Any]) -> str:
        return cls._string_value(source["source_id"]) if "source_id" in source else ""

    @staticmethod
    def _online_value(source: Mapping[str, Any]) -> bool:
        return bool(source["online"]) if "online" in source else False

    @staticmethod
    def _online_count_positive(snapshot: Mapping[str, Any]) -> bool:
        raw_count = snapshot["battery_online_source_count"] if "battery_online_source_count" in snapshot else 0
        return int(raw_count or 0) > 0

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
        previous_numeric = _EnergyCompanionDbusBridgeGrid._grid_numeric_value(previous_value)
        if previous_numeric is None:
            return float(numeric_value)
        if _EnergyCompanionDbusBridgeGrid._grid_smoothing_passthrough(normalized_alpha):
            return float(numeric_value)
        if _EnergyCompanionDbusBridgeGrid._grid_jump_exceeds_limit(
            numeric_value,
            previous_numeric,
            smoothing_max_jump_watts,
        ):
            return float(numeric_value)
        return _EnergyCompanionDbusBridgeGrid._grid_weighted_smoothed_value(
            numeric_value,
            previous_numeric,
            normalized_alpha,
        )

    @staticmethod
    def _grid_smoothing_passthrough(normalized_alpha: float) -> bool:
        return normalized_alpha in (0.0, 1.0)

    @staticmethod
    def _grid_jump_exceeds_limit(
        numeric_value: float,
        previous_value: float,
        smoothing_max_jump_watts: float,
    ) -> bool:
        max_jump_watts = float(smoothing_max_jump_watts)
        delta_watts = abs(float(numeric_value) - float(previous_value))
        return max_jump_watts > 0.0 and delta_watts > max_jump_watts

    @staticmethod
    def _grid_weighted_smoothed_value(
        numeric_value: float,
        previous_value: float,
        normalized_alpha: float,
    ) -> float:
        return float((normalized_alpha * numeric_value) + ((1.0 - normalized_alpha) * previous_value))

    @staticmethod
    def _grid_resolved_payload(value: float, connected: bool, last_good_at: float | None) -> dict[str, Any]:
        if not isinstance(connected, bool):
            raise TypeError("connected must be bool")
        return {
            "value": float(value),
            "connected": connected,
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
