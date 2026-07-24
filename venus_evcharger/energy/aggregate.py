# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregation helpers for multi-source battery and inverter snapshots.

This layer keeps source-level readings separate from cluster-level summaries.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .models import EnergyClusterSnapshot, EnergySourceDefinition, EnergySourceSnapshot
from .numeric import non_negative_optional_float, sum_optional
from .physical_sources import unique_weighted_soc_sources
from .profiles import energy_source_profile_details


_sum_optional = sum_optional


def _scoped_numeric_value(
    source: EnergySourceSnapshot,
    value_attr: str,
    scope_attr: str,
) -> tuple[float, str] | None:
    value = getattr(source, value_attr)
    if value is None:
        return None
    scope_key = str(getattr(source, scope_attr) or "").strip()
    return float(value), scope_key


def _sum_scoped_values(sources: tuple[EnergySourceSnapshot, ...], value_attr: str, scope_attr: str) -> float | None:
    numeric_values: list[float] = []
    seen_scope_keys: set[str] = set()
    for source in sources:
        scoped_value = _scoped_numeric_value(source, value_attr, scope_attr)
        if scoped_value is None:
            continue
        value, scope_key = scoped_value
        if _seen_scope_key(scope_key, seen_scope_keys):
            continue
        numeric_values.append(value)
    if not numeric_values:
        return None
    return sum(numeric_values)


def _seen_scope_key(scope_key: str, seen_scope_keys: set[str]) -> bool:
    if not scope_key:
        return False
    if scope_key in seen_scope_keys:
        return True
    seen_scope_keys.add(scope_key)
    return False


def _weighted_soc_source_values(
    source: EnergySourceSnapshot,
) -> tuple[float, float] | None:
    if source.soc is None or source.usable_capacity_wh is None or source.usable_capacity_wh <= 0.0:
        return None
    return float(source.soc), float(source.usable_capacity_wh)


def _weighted_soc(sources: tuple[EnergySourceSnapshot, ...]) -> tuple[float | None, float | None, int]:
    total_capacity = 0.0
    total_soc_energy = 0.0
    count = 0
    for source in unique_weighted_soc_sources(sources):
        weighted_values = _weighted_soc_source_values(source)
        if weighted_values is None:
            continue
        soc, capacity = weighted_values
        total_capacity += capacity
        total_soc_energy += capacity * soc
        count += 1
    if total_capacity <= 0.0:
        return None, None, count
    return total_soc_energy / total_capacity, total_capacity, count


def _average_confidence(sources: tuple[EnergySourceSnapshot, ...]) -> float | None:
    confidence_values = [float(source.confidence) for source in sources if source.confidence >= 0.0]
    if not confidence_values:
        return None
    return sum(confidence_values) / float(len(confidence_values))


def _effective_soc(combined_soc: float | None, sources: tuple[EnergySourceSnapshot, ...]) -> float | None:
    if combined_soc is not None:
        return combined_soc
    fallback_sources = _online_soc_sources(sources)
    if len(fallback_sources) != 1:
        return None
    fallback_soc = fallback_sources[0].soc
    assert fallback_soc is not None
    return float(fallback_soc)


def _online_soc_sources(
    sources: tuple[EnergySourceSnapshot, ...],
) -> list[EnergySourceSnapshot]:
    return [source for source in sources if source.online and source.soc is not None]


def _role_count(sources: tuple[EnergySourceSnapshot, ...], role: str) -> int:
    return sum(1 for source in sources if source.role == role)


def _non_negative_optional_float(value: Any) -> float | None:
    return non_negative_optional_float(value)


def _balance_profile_for_source(
    learning_profiles: Mapping[str, Any],
    source_id: str,
) -> Mapping[str, Any]:
    raw_profile = learning_profiles.get(source_id)
    return raw_profile if isinstance(raw_profile, Mapping) else {}


def _available_energy_above_reserve(
    capacity_wh: float | None,
    source_soc: float | None,
    reserve_floor_soc: float | None,
) -> float | None:
    normalized_floor = 0.0 if reserve_floor_soc is None else max(0.0, float(reserve_floor_soc))
    if capacity_wh is None or capacity_wh <= 0.0 or source_soc is None:
        return None
    available_fraction = max(0.0, float(source_soc) - normalized_floor) / 100.0
    return capacity_wh * available_fraction


def _discharge_balance_weight(
    source: EnergySourceSnapshot,
    reserve_floor_soc: float | None,
) -> tuple[float, float | None, str]:
    capacity_wh = _non_negative_optional_float(source.usable_capacity_wh)
    source_soc = _non_negative_optional_float(source.soc)
    available_energy_wh = _available_energy_above_reserve(capacity_wh, source_soc, reserve_floor_soc)
    if available_energy_wh is not None and available_energy_wh > 0.0:
        return available_energy_wh, available_energy_wh, "available_energy_above_reserve"
    if capacity_wh is not None and capacity_wh > 0.0:
        return capacity_wh, available_energy_wh, "usable_capacity_fallback"
    return 1.0, available_energy_wh, "uniform_fallback"


def _discharge_balance_eligible_source(
    source: EnergySourceSnapshot,
    profiles: Mapping[str, Any],
) -> dict[str, Any] | None:
    if source.role not in {"battery", "hybrid-inverter"} or not bool(source.online):
        return None
    profile = _balance_profile_for_source(profiles, source.source_id)
    reserve_floor_soc = _non_negative_optional_float(profile.get("reserve_band_floor_soc"))
    weight, available_energy_wh, weight_basis = _discharge_balance_weight(source, reserve_floor_soc)
    actual_discharge_w = max(0.0, float(source.discharge_power_w or 0.0))
    return {
        "source_id": source.source_id,
        "weight": float(weight),
        "weight_basis": weight_basis,
        "available_energy_wh": available_energy_wh,
        "reserve_floor_soc": reserve_floor_soc,
        "actual_discharge_w": actual_discharge_w,
    }


def _discharge_balance_source_metrics(
    item: Mapping[str, Any],
    total_discharge_w: float,
    total_weight: float,
) -> tuple[dict[str, Any], float]:
    target_share = float(item["weight"]) / float(total_weight)
    target_discharge_w = float(total_discharge_w) * target_share
    error_w = float(item["actual_discharge_w"]) - float(target_discharge_w)
    return (
        {
            "discharge_balance_eligible": True,
            "discharge_balance_weight": float(item["weight"]),
            "discharge_balance_weight_basis": str(item["weight_basis"]),
            "discharge_balance_available_energy_wh": item["available_energy_wh"],
            "discharge_balance_reserve_floor_soc": item["reserve_floor_soc"],
            "discharge_balance_target_distribution_mode": "capacity_reserve_weighted",
            "discharge_balance_target_share": target_share,
            "discharge_balance_target_power_w": target_discharge_w,
            "discharge_balance_actual_power_w": float(item["actual_discharge_w"]),
            "discharge_balance_error_w": error_w,
            "discharge_balance_relative_error": (
                error_w / float(total_discharge_w) if total_discharge_w > 0.0 else None
            ),
        },
        error_w,
    )


def _empty_discharge_balance_metrics() -> dict[str, Any]:
    return {
        "mode": "capacity_reserve_weighted",
        "target_distribution_mode": "capacity_reserve_weighted",
        "eligible_source_count": 0,
        "active_source_count": 0,
        "total_discharge_w": None,
        "error_w": None,
        "max_abs_error_w": None,
        "sources": {},
    }


def _eligible_discharge_balance_sources(
    normalized_sources: tuple[EnergySourceSnapshot, ...],
    profiles: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], float]:
    eligible_sources: list[dict[str, Any]] = []
    total_discharge_w = 0.0
    for source in normalized_sources:
        eligible_source = _discharge_balance_eligible_source(source, profiles)
        if eligible_source is None:
            continue
        total_discharge_w += float(eligible_source["actual_discharge_w"])
        eligible_sources.append(eligible_source)
    return eligible_sources, total_discharge_w


def _active_discharge_balance_source_count(
    eligible_sources: list[dict[str, Any]],
) -> int:
    return sum(1 for item in eligible_sources if float(item["actual_discharge_w"]) > 0.0)


def _discharge_balance_metrics_payload(
    eligible_sources: list[dict[str, Any]],
    total_discharge_w: float,
    source_metrics: dict[str, dict[str, Any]],
    total_abs_error_w: float,
    max_abs_error_w: float,
) -> dict[str, Any]:
    return {
        "mode": "capacity_reserve_weighted",
        "target_distribution_mode": "capacity_reserve_weighted",
        "eligible_source_count": len(eligible_sources),
        "active_source_count": _active_discharge_balance_source_count(eligible_sources),
        "total_discharge_w": float(total_discharge_w),
        "error_w": total_abs_error_w / 2.0,
        "max_abs_error_w": max_abs_error_w,
        "sources": source_metrics,
    }


def _discharge_balance_total_weight(
    eligible_sources: list[dict[str, Any]],
) -> float:
    return sum(float(item["weight"]) for item in eligible_sources)


def _discharge_balance_metric_totals(
    eligible_sources: list[dict[str, Any]],
    total_discharge_w: float,
    total_weight: float,
) -> tuple[dict[str, dict[str, Any]], float, float]:
    total_abs_error_w = 0.0
    max_abs_error_w = 0.0
    source_metrics: dict[str, dict[str, Any]] = {}
    for item in eligible_sources:
        metrics, error_w = _discharge_balance_source_metrics(item, total_discharge_w, total_weight)
        total_abs_error_w += abs(error_w)
        max_abs_error_w = max(max_abs_error_w, abs(error_w))
        source_metrics[str(item["source_id"])] = metrics
    return source_metrics, total_abs_error_w, max_abs_error_w


def derive_discharge_balance_metrics(
    sources: Iterable[EnergySourceSnapshot],
    learning_profiles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return fairness diagnostics for concurrent ESS discharge behavior."""
    normalized_sources = tuple(sources)
    profiles = dict(learning_profiles or {})
    eligible_sources, total_discharge_w = _eligible_discharge_balance_sources(
        normalized_sources, profiles
    )
    if not eligible_sources:
        return _empty_discharge_balance_metrics()
    total_weight = _discharge_balance_total_weight(eligible_sources)
    source_metrics, total_abs_error_w, max_abs_error_w = _discharge_balance_metric_totals(
        eligible_sources,
        total_discharge_w,
        total_weight,
    )
    return _discharge_balance_metrics_payload(
        eligible_sources,
        total_discharge_w,
        source_metrics,
        total_abs_error_w,
        max_abs_error_w,
    )


def _discharge_control_source_context(
    source: EnergySourceSnapshot,
    definition: EnergySourceDefinition | None,
) -> tuple[str, str, str]:
    if definition is None:
        return "", source.role, source.role
    return _defined_discharge_control_source_context(source, definition)


def _defined_discharge_control_source_context(
    source: EnergySourceSnapshot,
    definition: EnergySourceDefinition,
) -> tuple[str, str, str]:
    profile_name = str(definition.profile_name or "").strip()
    connector_type = _normalized_definition_text(definition.connector_type, source.role)
    role = _normalized_definition_text(definition.role, source.role)
    return profile_name, connector_type, role


def _normalized_definition_text(value: object, fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text or fallback


def _discharge_control_reason(controllable_role: bool, write_support: str) -> str:
    if not controllable_role:
        return "role_not_targeted"
    if write_support == "supported":
        return "profile_write_supported"
    if write_support == "experimental":
        return "profile_write_experimental"
    return "profile_write_unsupported"


def _discharge_control_source_metrics(
    source: EnergySourceSnapshot,
    definition: EnergySourceDefinition | None,
) -> dict[str, Any]:
    profile_name, connector_type, role = _discharge_control_source_context(source, definition)
    profile_details = energy_source_profile_details(profile_name)
    raw_write_support = profile_details.get("write_support")
    write_support = str(raw_write_support).strip().lower() if raw_write_support else "unsupported"
    controllable_role = role in {"battery", "hybrid-inverter"}
    control_candidate = controllable_role and write_support in {"supported", "experimental"}
    control_ready = control_candidate and bool(source.online)
    return {
        "discharge_balance_control_profile_name": profile_name,
        "discharge_balance_control_connector_type": connector_type,
        "discharge_balance_control_support": write_support,
        "discharge_balance_control_candidate": control_candidate,
        "discharge_balance_control_ready": control_ready,
        "discharge_balance_control_reason": _discharge_control_reason(controllable_role, write_support),
        "discharge_balance_control_role_targeted": controllable_role,
    }


def _discharge_control_counter_updates(metrics: Mapping[str, Any]) -> tuple[int, int, int, int]:
    candidate = 1 if bool(metrics["discharge_balance_control_candidate"]) else 0
    ready = 1 if bool(metrics["discharge_balance_control_ready"]) else 0
    support = str(metrics["discharge_balance_control_support"])
    targeted = bool(metrics["discharge_balance_control_role_targeted"])
    supported = 1 if _targeted_write_support(targeted, support, "supported") else 0
    experimental = 1 if _targeted_write_support(targeted, support, "experimental") else 0
    return candidate, ready, supported, experimental


def _targeted_write_support(targeted: bool, support: str, expected: str) -> bool:
    return targeted and support == expected


def derive_discharge_control_metrics(
    sources: Iterable[EnergySourceSnapshot],
    source_definitions: Mapping[str, EnergySourceDefinition] | None = None,
) -> dict[str, Any]:
    """Return diagnostic write-capability hints for per-source discharge coordination."""
    normalized_sources = tuple(sources)
    definitions = dict(source_definitions or {})
    source_metrics: dict[str, dict[str, Any]] = {}
    candidate_count = 0
    ready_count = 0
    supported_count = 0
    experimental_count = 0
    for source in normalized_sources:
        metrics = _discharge_control_source_metrics(source, definitions.get(source.source_id))
        candidate, ready, supported, experimental = _discharge_control_counter_updates(metrics)
        candidate_count += candidate
        ready_count += ready
        supported_count += supported
        experimental_count += experimental
        source_metrics[source.source_id] = metrics
    return {
        "control_candidate_count": candidate_count,
        "control_ready_count": ready_count,
        "supported_control_source_count": supported_count,
        "experimental_control_source_count": experimental_count,
        "sources": source_metrics,
    }


def _aggregate_power_totals(
    normalized_sources: tuple[EnergySourceSnapshot, ...],
) -> dict[str, float | None]:
    charge_power = _summed_source_attr(normalized_sources, "charge_power_w")
    discharge_power = _summed_source_attr(normalized_sources, "discharge_power_w")
    charge_limit = _summed_source_attr(normalized_sources, "charge_limit_power_w")
    discharge_limit = _summed_source_attr(normalized_sources, "discharge_limit_power_w")
    net_battery_power = _summed_source_attr(normalized_sources, "net_battery_power_w")
    return {
        "combined_charge_power_w": charge_power,
        "combined_discharge_power_w": discharge_power,
        "combined_charge_limit_power_w": charge_limit,
        "combined_discharge_limit_power_w": discharge_limit,
        "combined_net_battery_power_w": net_battery_power,
        "combined_ac_power_w": _sum_scoped_values(normalized_sources, "ac_power_w", "ac_power_scope_key"),
        "combined_pv_input_power_w": _sum_scoped_values(
            normalized_sources, "pv_input_power_w", "pv_input_power_scope_key"
        ),
        "combined_grid_interaction_w": _sum_scoped_values(
            normalized_sources,
            "grid_interaction_w",
            "grid_interaction_scope_key",
        ),
    }


def _summed_source_attr(
    normalized_sources: tuple[EnergySourceSnapshot, ...],
    attr_name: str,
) -> float | None:
    return _sum_optional(getattr(source, attr_name) for source in normalized_sources)


def aggregate_energy_sources(sources: Iterable[EnergySourceSnapshot]) -> EnergyClusterSnapshot:
    """Return one combined energy cluster snapshot from normalized source snapshots."""
    normalized_sources = tuple(sources)
    combined_soc, combined_capacity_wh, valid_soc_source_count = _weighted_soc(normalized_sources)
    power_totals = _aggregate_power_totals(normalized_sources)
    return EnergyClusterSnapshot(
        effective_soc=_effective_soc(combined_soc, normalized_sources),
        combined_soc=combined_soc,
        combined_usable_capacity_wh=combined_capacity_wh,
        combined_charge_power_w=power_totals["combined_charge_power_w"],
        combined_discharge_power_w=power_totals["combined_discharge_power_w"],
        combined_charge_limit_power_w=power_totals["combined_charge_limit_power_w"],
        combined_discharge_limit_power_w=power_totals["combined_discharge_limit_power_w"],
        combined_net_battery_power_w=power_totals["combined_net_battery_power_w"],
        combined_ac_power_w=power_totals["combined_ac_power_w"],
        combined_pv_input_power_w=power_totals["combined_pv_input_power_w"],
        combined_grid_interaction_w=power_totals["combined_grid_interaction_w"],
        average_confidence=_average_confidence(normalized_sources),
        source_count=len(normalized_sources),
        online_source_count=sum(1 for source in normalized_sources if source.online),
        valid_soc_source_count=valid_soc_source_count,
        battery_source_count=_role_count(normalized_sources, "battery"),
        hybrid_inverter_source_count=_role_count(normalized_sources, "hybrid-inverter"),
        inverter_source_count=_role_count(normalized_sources, "inverter"),
        sources=normalized_sources,
    )
