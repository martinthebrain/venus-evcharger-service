# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral contracts for externally writable control targets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from venus_evcharger.control.models import ControlCommandName
else:
    ControlCommandName = str

ControlValueKind = Literal[
    "binary",
    "current",
    "float",
    "integer",
    "mode",
    "phase_selection",
    "string",
]


@dataclass(frozen=True, slots=True)
class ControlTargetContract:
    """One stable domain target exposed through external control adapters."""

    target: str
    writable: bool = True
    rollback_snapshot: bool = True
    direct_command: ControlCommandName | None = None
    value_kind: ControlValueKind | None = None
    required_order: int | None = None


_DIRECT_CONTROL_TARGETS = (
    ControlTargetContract("mode", direct_command="set_mode", value_kind="mode", required_order=0),
    ControlTargetContract("auto_start", direct_command="set_auto_start", value_kind="binary", required_order=4),
    ControlTargetContract("start_stop", direct_command="set_start_stop", value_kind="binary", required_order=1),
    ControlTargetContract("enable", direct_command="set_enable", value_kind="binary", required_order=2),
    ControlTargetContract(
        "phase_selection",
        direct_command="set_phase_selection",
        value_kind="phase_selection",
    ),
)

_PHASE_STATUS_TARGETS = (
    "phase_selection_active",
    "supported_phase_selections",
    "auto_phase_lockout_active",
    "auto_phase_lockout_target",
    "auto_phase_lockout_reason",
    "auto_phase_supported_configured",
    "auto_phase_supported_effective",
    "auto_phase_degraded_active",
)

_CONTACTOR_STATUS_TARGETS = (
    "auto_contactor_fault_count",
    "auto_contactor_lockout_active",
    "auto_contactor_lockout_reason",
    "auto_contactor_lockout_source",
    "auto_contactor_lockout_age",
)

_CURRENT_SETTING_TARGETS = (
    ControlTargetContract("set_current", value_kind="current", required_order=3),
    ControlTargetContract("min_current", value_kind="current"),
    ControlTargetContract("max_current", value_kind="current"),
)

_PHASE_LOCKOUT_RESET_CONTRACT = ControlTargetContract(
    "auto_phase_lockout_reset",
    direct_command="reset_phase_lockout",
    value_kind="binary",
)

_CONTACTOR_LOCKOUT_RESET_CONTRACT = ControlTargetContract(
    "auto_contactor_lockout_reset",
    direct_command="reset_contactor_lockout",
    value_kind="binary",
)

_AUTO_RUNTIME_TARGET_SPECS: tuple[tuple[str, ControlValueKind], ...] = (
    ("auto_start_surplus_watts", "float"),
    ("auto_stop_surplus_watts", "float"),
    ("auto_min_soc", "float"),
    ("auto_resume_soc", "float"),
    ("auto_start_delay_seconds", "float"),
    ("auto_stop_delay_seconds", "float"),
    ("auto_scheduled_enabled_days", "string"),
    ("auto_scheduled_fallback_delay_seconds", "float"),
    ("auto_scheduled_latest_end_time", "string"),
    ("auto_scheduled_night_current", "float"),
    ("auto_dbus_backoff_base_seconds", "float"),
    ("auto_dbus_backoff_max_seconds", "float"),
    ("auto_grid_recovery_start_seconds", "float"),
    ("auto_stop_surplus_delay_seconds", "float"),
    ("auto_stop_surplus_volatility_low_watts", "float"),
    ("auto_stop_surplus_volatility_high_watts", "float"),
    ("auto_reference_charge_power_watts", "float"),
    ("auto_learn_charge_power_enabled", "binary"),
    ("auto_learn_charge_power_min_watts", "float"),
    ("auto_learn_charge_power_alpha", "float"),
    ("auto_learn_charge_power_start_delay_seconds", "float"),
    ("auto_learn_charge_power_window_seconds", "float"),
    ("auto_learn_charge_power_max_age_seconds", "float"),
    ("auto_phase_switching", "binary"),
    ("auto_phase_prefer_lowest_when_idle", "binary"),
    ("auto_phase_upshift_delay_seconds", "float"),
    ("auto_phase_downshift_delay_seconds", "float"),
    ("auto_phase_upshift_headroom_watts", "float"),
    ("auto_phase_downshift_margin_watts", "float"),
    ("auto_phase_mismatch_retry_seconds", "float"),
    ("auto_phase_mismatch_lockout_count", "integer"),
    ("auto_phase_mismatch_lockout_seconds", "float"),
)

_SOFTWARE_UPDATE_CONTRACT = ControlTargetContract(
    "auto_software_update_run",
    rollback_snapshot=False,
    direct_command="trigger_software_update",
    value_kind="binary",
)

CONTROL_TARGET_CONTRACTS = (
    *_DIRECT_CONTROL_TARGETS,
    *(ControlTargetContract(target, writable=False) for target in _PHASE_STATUS_TARGETS),
    _PHASE_LOCKOUT_RESET_CONTRACT,
    *(ControlTargetContract(target, writable=False) for target in _CONTACTOR_STATUS_TARGETS),
    _CONTACTOR_LOCKOUT_RESET_CONTRACT,
    *_CURRENT_SETTING_TARGETS,
    *(ControlTargetContract(target, value_kind=value_kind) for target, value_kind in _AUTO_RUNTIME_TARGET_SPECS),
    _SOFTWARE_UPDATE_CONTRACT,
)


def _target_index(contracts: tuple[ControlTargetContract, ...]) -> Mapping[str, ControlTargetContract]:
    indexed = {contract.target: contract for contract in contracts}
    if len(indexed) != len(contracts):
        raise ValueError("Control target registry contains duplicate targets.")
    return MappingProxyType(indexed)


CONTROL_TARGET_BY_NAME = _target_index(CONTROL_TARGET_CONTRACTS)
CONTROL_WRITABLE_TARGETS = frozenset(
    contract.target for contract in CONTROL_TARGET_CONTRACTS if contract.writable
)
CONTROL_WRITE_SNAPSHOT_TARGETS = tuple(
    contract.target for contract in CONTROL_TARGET_CONTRACTS if contract.rollback_snapshot
)
CONTROL_REQUIRED_TARGETS = tuple(
    contract.target
    for contract in sorted(
        (item for item in CONTROL_TARGET_CONTRACTS if item.required_order is not None),
        key=lambda item: item.required_order if item.required_order is not None else -1,
    )
)
CONTROL_DIRECT_TARGET_COMMANDS: Mapping[str, ControlCommandName] = MappingProxyType(
    {
        contract.target: contract.direct_command
        for contract in CONTROL_TARGET_CONTRACTS
        if contract.direct_command is not None
    }
)
CONTROL_COMMAND_DEFAULT_TARGETS: Mapping[ControlCommandName, str] = MappingProxyType(
    {command_name: target for target, command_name in CONTROL_DIRECT_TARGET_COMMANDS.items()}
)
CONTROL_CURRENT_SETTING_TARGETS = frozenset(
    contract.target for contract in _CURRENT_SETTING_TARGETS
)
CONTROL_PHASE_SELECTIONS = frozenset({"P1", "P1_P2", "P1_P2_P3"})
CONTROL_AUTO_RUNTIME_TARGETS = frozenset(target for target, _value_kind in _AUTO_RUNTIME_TARGET_SPECS)

_NON_DIRECT_COMMAND_NAMES: tuple[ControlCommandName, ...] = (
    "set_auto_runtime_setting",
    "set_current_setting",
)
CONTROL_COMMAND_NAMES: frozenset[ControlCommandName] = frozenset(
    (*_NON_DIRECT_COMMAND_NAMES, *CONTROL_DIRECT_TARGET_COMMANDS.values())
)
CONTROL_BINARY_COMMANDS: frozenset[ControlCommandName] = frozenset(
    contract.direct_command
    for contract in CONTROL_TARGET_CONTRACTS
    if contract.direct_command is not None and contract.value_kind == "binary"
)


def _auto_runtime_targets(value_kind: ControlValueKind) -> frozenset[str]:
    return frozenset(
        contract.target
        for contract in CONTROL_TARGET_CONTRACTS
        if contract.direct_command is None
        and contract.target in CONTROL_AUTO_RUNTIME_TARGETS
        and contract.value_kind == value_kind
    )


CONTROL_FLOAT_AUTO_RUNTIME_TARGETS = _auto_runtime_targets("float")
CONTROL_STRING_AUTO_RUNTIME_TARGETS = _auto_runtime_targets("string")
CONTROL_BINARY_AUTO_RUNTIME_TARGETS = _auto_runtime_targets("binary")
CONTROL_INTEGER_AUTO_RUNTIME_TARGETS = _auto_runtime_targets("integer")
_AUTO_RUNTIME_TARGET_GROUPS: tuple[tuple[ControlValueKind, frozenset[str]], ...] = (
    ("float", CONTROL_FLOAT_AUTO_RUNTIME_TARGETS),
    ("string", CONTROL_STRING_AUTO_RUNTIME_TARGETS),
    ("binary", CONTROL_BINARY_AUTO_RUNTIME_TARGETS),
    ("integer", CONTROL_INTEGER_AUTO_RUNTIME_TARGETS),
)
CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_TARGET: Mapping[str, ControlValueKind] = MappingProxyType(
    {
        target: value_kind
        for value_kind, targets in _AUTO_RUNTIME_TARGET_GROUPS
        for target in targets
    }
)

CONTROL_API_STATE_ENDPOINTS = frozenset(
    {
        "/v1/state/automation",
        "/v1/state/build",
        "/v1/state/config-effective",
        "/v1/state/contracts",
        "/v1/state/dbus-diagnostics",
        "/v1/state/health",
        "/v1/state/healthz",
        "/v1/state/operational",
        "/v1/state/runtime",
        "/v1/state/summary",
        "/v1/state/topology",
        "/v1/state/update",
        "/v1/state/version",
        "/v1/state/victron-bias-recommendation",
    }
)


__all__ = [
    "CONTROL_API_STATE_ENDPOINTS",
    "CONTROL_AUTO_RUNTIME_TARGETS",
    "CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_TARGET",
    "CONTROL_BINARY_AUTO_RUNTIME_TARGETS",
    "CONTROL_BINARY_COMMANDS",
    "CONTROL_COMMAND_DEFAULT_TARGETS",
    "CONTROL_COMMAND_NAMES",
    "CONTROL_CURRENT_SETTING_TARGETS",
    "CONTROL_DIRECT_TARGET_COMMANDS",
    "CONTROL_FLOAT_AUTO_RUNTIME_TARGETS",
    "CONTROL_INTEGER_AUTO_RUNTIME_TARGETS",
    "CONTROL_PHASE_SELECTIONS",
    "CONTROL_REQUIRED_TARGETS",
    "CONTROL_STRING_AUTO_RUNTIME_TARGETS",
    "CONTROL_TARGET_BY_NAME",
    "CONTROL_TARGET_CONTRACTS",
    "CONTROL_WRITABLE_TARGETS",
    "CONTROL_WRITE_SNAPSHOT_TARGETS",
    "ControlTargetContract",
    "ControlValueKind",
]
