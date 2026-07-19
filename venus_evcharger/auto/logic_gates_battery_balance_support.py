# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Any, Mapping

from .component_context import AutoDecisionContext
from .logic_gates_battery_learning import AutoBatteryLearning

_BIAS_MODES = {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}
_COORDINATION_SUPPORT_MODES = {"supported_only", "allow_experimental"}


def _service_bool(service: Any, name: str) -> bool:
    if not hasattr(service, name):
        return False
    return bool(getattr(service, name))


def _service_non_negative_float(service: Any, name: str) -> float:
    if not hasattr(service, name):
        return 0.0
    return max(0.0, float(getattr(service, name) or 0.0))


def _service_mode(service: Any, name: str, valid_modes: set[str], default: str) -> str:
    if not hasattr(service, name):
        return default
    raw_value = getattr(service, name)
    if raw_value is None:
        return default
    raw_mode = str(raw_value).strip().lower()
    if raw_mode in valid_modes:
        return raw_mode
    return default


def _mapping_count(values: Mapping[str, Any], key: str) -> int:
    if key not in values:
        return 0
    return int(values[key] or 0)


class AutoBatteryBalancePolicy:
    """Evaluate discharge-balance warnings, gates, and penalties."""

    def __init__(self, context: AutoDecisionContext, learning: AutoBatteryLearning) -> None:
        self.service = context.service
        self.learning = learning

    def _battery_discharge_balance_policy_context(
        self,
        cluster: Mapping[str, Any],
        *,
        expected_export_w: float | None,
        reserve_floor_soc: float | None,
    ) -> tuple[bool, float, float, str, bool, float, float]:
        if not self._battery_discharge_balance_policy_enabled():
            return False, 0.0, 0.0, "always", False, 0.0, 0.0
        error_w, eligible_source_count, active_source_count = self._battery_discharge_balance_policy_counts(cluster)
        warn_threshold_w, bias_start_error_w, bias_max_penalty_w = self._battery_discharge_balance_policy_thresholds()
        bias_mode = self._discharge_balance_bias_mode()
        gate_active = self._discharge_balance_bias_gate_active(
            bias_mode=bias_mode,
            cluster=cluster,
            expected_export_w=expected_export_w,
            reserve_floor_soc=reserve_floor_soc,
            reserve_margin_soc=self._battery_discharge_balance_reserve_margin_soc(),
        )
        warning_active = self._battery_discharge_balance_warning_active(
            eligible_source_count,
            active_source_count,
            error_w,
            warn_threshold_w,
        )
        bias_penalty_w = self._battery_discharge_balance_penalty_w(
            eligible_source_count=eligible_source_count,
            active_source_count=active_source_count,
            gate_active=gate_active,
            error_w=error_w,
            start_error_w=bias_start_error_w,
            max_penalty_w=bias_max_penalty_w,
        )
        return (
            bool(warning_active),
            float(error_w),
            float(warn_threshold_w),
            bias_mode,
            bool(gate_active),
            float(bias_start_error_w),
            float(bias_penalty_w),
        )

    def _battery_discharge_balance_policy_enabled(self) -> bool:
        return _service_bool(self.service, "auto_battery_discharge_balance_policy_enabled")

    def _battery_discharge_balance_policy_counts(
        self,
        cluster: Mapping[str, Any],
    ) -> tuple[float, int, int]:
        return (
            self.learning._non_negative_optional_float(cluster.get("battery_discharge_balance_error_w")) or 0.0,
            _mapping_count(cluster, "battery_discharge_balance_eligible_source_count"),
            _mapping_count(cluster, "battery_discharge_balance_active_source_count"),
        )

    def _battery_discharge_balance_policy_thresholds(self) -> tuple[float, float, float]:
        svc = self.service
        return (
            _service_non_negative_float(svc, "auto_battery_discharge_balance_warn_error_watts"),
            _service_non_negative_float(svc, "auto_battery_discharge_balance_bias_start_error_watts"),
            _service_non_negative_float(svc, "auto_battery_discharge_balance_bias_max_penalty_watts"),
        )

    def _battery_discharge_balance_reserve_margin_soc(self) -> float:
        return _service_non_negative_float(self.service, "auto_battery_discharge_balance_bias_reserve_margin_soc")

    @staticmethod
    def _battery_discharge_balance_warning_active(
        eligible_source_count: int,
        active_source_count: int,
        error_w: float,
        warn_threshold_w: float,
    ) -> bool:
        return eligible_source_count >= 2 and active_source_count >= 1 and error_w > 0.0 and error_w >= warn_threshold_w

    @staticmethod
    def _battery_discharge_balance_penalty_w(
        *,
        eligible_source_count: int,
        active_source_count: int,
        gate_active: bool,
        error_w: float,
        start_error_w: float,
        max_penalty_w: float,
    ) -> float:
        if not _battery_discharge_balance_penalty_inputs_valid(
            eligible_source_count,
            active_source_count,
            gate_active,
            max_penalty_w,
        ):
            return 0.0
        return _battery_discharge_balance_penalty_value(error_w, start_error_w, max_penalty_w)

    def _discharge_balance_bias_mode(self) -> str:
        return _service_mode(self.service, "auto_battery_discharge_balance_bias_mode", _BIAS_MODES, "always")

    @staticmethod
    def _battery_discharge_balance_coordination_advisory(
        cluster: Mapping[str, Any],
        *,
        warning_active: bool,
    ) -> tuple[str, bool, str]:
        counts = _battery_discharge_balance_coordination_counts(cluster)
        for evaluator in (
            _battery_discharge_balance_coordination_not_needed,
            _battery_discharge_balance_coordination_supported,
            _battery_discharge_balance_coordination_experimental,
            _battery_discharge_balance_coordination_blocked_by_availability,
            _battery_discharge_balance_coordination_partial,
        ):
            advisory = evaluator(counts, warning_active)
            if advisory is not None:
                return advisory
        return "observe_only", bool(warning_active), "no_configured_source_offers_a_write_path"

    def _battery_discharge_balance_coordination_policy_context(
        self,
        cluster: Mapping[str, Any],
        *,
        feasibility: str,
    ) -> tuple[bool, str, bool, float, float]:
        support_mode = self._discharge_balance_coordination_support_mode()
        if not self._battery_discharge_balance_coordination_enabled():
            return False, support_mode, False, 0.0, 0.0
        error_w, control_ready_count = self._battery_discharge_balance_coordination_counts(cluster)
        start_error_w, max_penalty_w = self._battery_discharge_balance_coordination_thresholds()
        gate_active = self._battery_discharge_balance_coordination_gate_active(
            support_mode=support_mode,
            feasibility=feasibility,
            control_ready_count=control_ready_count,
        )
        penalty_w = _battery_discharge_balance_coordination_penalty_w(
            gate_active=gate_active,
            error_w=error_w,
            start_error_w=start_error_w,
            max_penalty_w=max_penalty_w,
        )
        return True, support_mode, bool(gate_active), float(start_error_w), float(penalty_w)

    def _battery_discharge_balance_coordination_enabled(self) -> bool:
        return _service_bool(self.service, "auto_battery_discharge_balance_coordination_enabled")

    def _battery_discharge_balance_coordination_counts(
        self,
        cluster: Mapping[str, Any],
    ) -> tuple[float, int]:
        return (
            self.learning._non_negative_optional_float(cluster.get("battery_discharge_balance_error_w")) or 0.0,
            _mapping_count(cluster, "battery_discharge_balance_control_ready_count"),
        )

    def _battery_discharge_balance_coordination_thresholds(self) -> tuple[float, float]:
        svc = self.service
        return (
            _service_non_negative_float(svc, "auto_battery_discharge_balance_coordination_start_error_watts"),
            _service_non_negative_float(svc, "auto_battery_discharge_balance_coordination_max_penalty_watts"),
        )

    @staticmethod
    def _battery_discharge_balance_coordination_gate_active(
        *,
        support_mode: str,
        feasibility: str,
        control_ready_count: int,
    ) -> bool:
        return control_ready_count >= 2 and feasibility in _battery_discharge_balance_allowed_feasibilities(support_mode)

    def _discharge_balance_coordination_support_mode(self) -> str:
        return _service_mode(
            self.service,
            "auto_battery_discharge_balance_coordination_support_mode",
            _COORDINATION_SUPPORT_MODES,
            "supported_only",
        )

    def _discharge_balance_bias_gate_active(
        self,
        *,
        bias_mode: str,
        cluster: Mapping[str, Any],
        expected_export_w: float | None,
        reserve_floor_soc: float | None,
        reserve_margin_soc: float,
    ) -> bool:
        export_active = self._discharge_balance_export_active(expected_export_w)
        reserve_gate_active = self._discharge_balance_reserve_gate_active(
            cluster,
            reserve_floor_soc,
            reserve_margin_soc,
        )
        return _discharge_balance_bias_mode_active(bias_mode, export_active, reserve_gate_active)

    @staticmethod
    def _discharge_balance_export_active(expected_export_w: float | None) -> bool:
        return bool((expected_export_w or 0.0) > 0.0)

    def _discharge_balance_reserve_gate_active(
        self,
        cluster: Mapping[str, Any],
        reserve_floor_soc: float | None,
        reserve_margin_soc: float,
    ) -> bool:
        combined_soc = self.learning._non_negative_optional_float(cluster.get("battery_combined_soc"))
        return (
            combined_soc is not None
            and reserve_floor_soc is not None
            and combined_soc >= (float(reserve_floor_soc) + float(reserve_margin_soc))
        )


def _battery_discharge_balance_penalty_scale(error_w: float, start_error_w: float) -> float:
    return min(max((error_w - start_error_w) / max(start_error_w, 1.0), 0.0), 1.0)


def _battery_discharge_balance_penalty_inputs_valid(
    eligible_source_count: int,
    active_source_count: int,
    gate_active: bool,
    max_penalty_w: float,
) -> bool:
    return eligible_source_count >= 2 and active_source_count >= 1 and gate_active and max_penalty_w > 0.0


def _battery_discharge_balance_penalty_value(
    error_w: float,
    start_error_w: float,
    max_penalty_w: float,
) -> float:
    if start_error_w <= 0.0:
        return _battery_discharge_balance_zero_start_penalty(error_w, max_penalty_w)
    return max_penalty_w * _battery_discharge_balance_penalty_scale(error_w, start_error_w)


def _battery_discharge_balance_zero_start_penalty(error_w: float, max_penalty_w: float) -> float:
    if error_w > 0.0:
        return max_penalty_w
    return 0.0


def _battery_discharge_balance_coordination_penalty_w(
    *,
    gate_active: bool,
    error_w: float,
    start_error_w: float,
    max_penalty_w: float,
) -> float:
    if not gate_active:
        return 0.0
    return _battery_discharge_balance_penalty_value(error_w, start_error_w, max(0.0, max_penalty_w))


def _battery_discharge_balance_coordination_counts(cluster: Mapping[str, Any]) -> dict[str, int]:
    return {
        "eligible_source_count": _battery_discharge_balance_cluster_count(
            cluster,
            "battery_discharge_balance_eligible_source_count",
        ),
        "control_candidate_count": _battery_discharge_balance_cluster_count(
            cluster,
            "battery_discharge_balance_control_candidate_count",
        ),
        "control_ready_count": _battery_discharge_balance_cluster_count(
            cluster,
            "battery_discharge_balance_control_ready_count",
        ),
        "supported_count": _battery_discharge_balance_cluster_count(
            cluster,
            "battery_discharge_balance_supported_control_source_count",
        ),
        "experimental_count": _battery_discharge_balance_cluster_count(
            cluster,
            "battery_discharge_balance_experimental_control_source_count",
        ),
    }


def _battery_discharge_balance_cluster_count(cluster: Mapping[str, Any], key: str) -> int:
    return _mapping_count(cluster, key)


def _battery_discharge_balance_coordination_not_needed(
    counts: Mapping[str, int],
    warning_active: bool,
) -> tuple[str, bool, str] | None:
    if counts["eligible_source_count"] >= 2:
        return None
    return "not_needed", False, "single_source_or_insufficient_sources"


def _battery_discharge_balance_coordination_supported(
    counts: Mapping[str, int],
    warning_active: bool,
) -> tuple[str, bool, str] | None:
    if counts["supported_count"] < 2 or counts["control_ready_count"] < 2:
        return None
    return "supported", False, "multiple_supported_control_sources_ready"


def _battery_discharge_balance_coordination_experimental(
    counts: Mapping[str, int],
    warning_active: bool,
) -> tuple[str, bool, str] | None:
    if counts["control_ready_count"] < 2:
        return None
    if (counts["supported_count"] + counts["experimental_count"]) < 2:
        return None
    if counts["experimental_count"] <= 0:
        return None
    return "experimental", bool(warning_active), "coordination_depends_on_experimental_write_paths"


def _battery_discharge_balance_coordination_blocked_by_availability(
    counts: Mapping[str, int],
    warning_active: bool,
) -> tuple[str, bool, str] | None:
    if counts["control_candidate_count"] < 2:
        return None
    if counts["control_ready_count"] >= 2:
        return None
    return "blocked_by_source_availability", bool(warning_active), "candidate_sources_not_ready"


def _battery_discharge_balance_coordination_partial(
    counts: Mapping[str, int],
    warning_active: bool,
) -> tuple[str, bool, str] | None:
    if counts["control_candidate_count"] < 1:
        return None
    return "partial", bool(warning_active), "only_some_sources_offer_a_write_path"


def _battery_discharge_balance_allowed_feasibilities(support_mode: str) -> set[str]:
    if support_mode == "allow_experimental":
        return {"supported", "experimental"}
    return {"supported"}


def _discharge_balance_bias_mode_active(
    bias_mode: str,
    export_active: bool,
    reserve_gate_active: bool,
) -> bool:
    if bias_mode == "export_only":
        return export_active
    if bias_mode == "above_reserve_band":
        return reserve_gate_active
    if bias_mode == "export_and_above_reserve_band":
        return export_active and reserve_gate_active
    return True
