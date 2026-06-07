# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinator switch backend that maps concrete child adapters to phases."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

from .base import SwitchBackend
from .config_file import config_section, load_required_backend_config
from .models import (
    PhaseSelection,
    SwitchCapabilities,
    SwitchState,
    SwitchingMode,
    normalize_phase_selection_tuple,
)

_PHASE_LABELS: tuple[str, ...] = ("P1", "P2", "P3")
_PHASE_SELECTION_MEMBERS: dict[PhaseSelection, tuple[str, ...]] = {
    "P1": ("P1",),
    "P1_P2": ("P1", "P2"),
    "P1_P2_P3": ("P1", "P2", "P3"),
}


@dataclass(frozen=True)
class SwitchGroupMember:
    """One configured child switch adapter assigned to a concrete phase."""

    phase_label: str
    backend_type: str
    config_path: Path


@dataclass(frozen=True)
class SwitchGroupSettings:
    """Normalized switch-group configuration plus aggregated capabilities."""

    phase_members: dict[str, SwitchGroupMember]
    phase_switch_targets: dict[PhaseSelection, tuple[str, ...]]
    supported_phase_selections: tuple[PhaseSelection, ...]
    switching_mode: SwitchingMode
    requires_charge_pause_for_phase_change: bool
    max_direct_switch_power_w: float | None


def _config(config_path: str) -> configparser.ConfigParser:
    """Load one switch-group config file."""
    return load_required_backend_config(config_path, "switch group")


def _normalized_phase_label(raw_key: object) -> str | None:
    """Return one supported child-phase label from a config key."""
    normalized = str(raw_key).strip().upper()
    return normalized if normalized in _PHASE_LABELS else None


def _resolved_member_path(group_config_path: str, raw_value: object) -> Path:
    """Return one normalized child backend path relative to the group config file."""
    configured = str(raw_value).strip()
    if not configured:
        raise ValueError("Switch group member config path may not be empty")
    path = Path(configured)
    if path.is_absolute():
        return path
    return Path(group_config_path).resolve().parent / path


def _member_backend_type(config_path: Path) -> str:
    """Return one normalized child switch backend type from its own config file."""
    parser = _config(str(config_path))
    if parser.has_section("Adapter"):
        return parser["Adapter"].get("Type", "shelly_combined").strip().lower()
    return parser["DEFAULT"].get("Type", "shelly_combined").strip().lower()


def _phase_members(group_config_path: str, members: configparser.SectionProxy) -> dict[str, SwitchGroupMember]:
    """Return the configured phase-to-child-adapter mapping."""
    phase_members: dict[str, SwitchGroupMember] = {}
    for raw_key, raw_value in members.items():
        phase_label = _required_phase_label(raw_key)
        config_path = _resolved_member_path(group_config_path, raw_value)
        phase_members[phase_label] = SwitchGroupMember(
            phase_label=phase_label,
            backend_type=_member_backend_type(config_path),
            config_path=config_path,
        )
    _validate_phase_members(phase_members)
    return phase_members


def _required_phase_label(raw_key: object) -> str:
    """Return one validated phase label from a member-config key."""
    phase_label = _normalized_phase_label(raw_key)
    if phase_label is None:
        raise ValueError(f"Unsupported switch-group member key '{raw_key}'")
    return phase_label


def _validate_phase_members(phase_members: Mapping[str, SwitchGroupMember]) -> None:
    """Validate required member combinations for switch-group backends."""
    if "P1" not in phase_members:
        raise ValueError("Switch group requires a member config for P1")
    if "P3" in phase_members and "P2" not in phase_members:
        raise ValueError("Switch group requires P2 when P3 is configured")


def _available_supported_phase_selections(phase_members: Mapping[str, SwitchGroupMember]) -> tuple[PhaseSelection, ...]:
    """Return the maximally available phase layouts from the configured member set."""
    if "P2" not in phase_members:
        return ("P1",)
    if "P3" not in phase_members:
        return ("P1", "P1_P2")
    return ("P1", "P1_P2", "P1_P2_P3")


def _supported_phase_selections(
    capabilities: configparser.SectionProxy,
    phase_members: Mapping[str, SwitchGroupMember],
) -> tuple[PhaseSelection, ...]:
    """Return the requested supported phase layouts constrained to configured members."""
    available = _available_supported_phase_selections(phase_members)
    requested = normalize_phase_selection_tuple(
        capabilities.get("SupportedPhaseSelections", ",".join(available)),
        available,
    )
    normalized: list[PhaseSelection] = []
    for selection in requested:
        _validate_requested_phase_selection(selection, available)
        if selection not in normalized:
            normalized.append(selection)
    _validate_supported_phase_selection_list(normalized)
    return tuple(normalized) or available


def _validate_requested_phase_selection(
    selection: PhaseSelection,
    available: tuple[PhaseSelection, ...],
) -> None:
    """Validate one requested supported phase selection."""
    if selection not in available:
        raise ValueError(
            f"Switch group requested unsupported phase selection '{selection}' for configured members"
        )


def _validate_supported_phase_selection_list(normalized: list[PhaseSelection]) -> None:
    """Ensure the normalized supported phase-selection list remains usable."""
    if "P1" not in normalized:
        raise ValueError("Switch group SupportedPhaseSelections must include P1")


def _phase_switch_targets(
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> dict[PhaseSelection, tuple[str, ...]]:
    """Return the effective child members targeted by every supported layout."""
    return {
        selection: _PHASE_SELECTION_MEMBERS[selection]
        for selection in supported_phase_selections
    }


def _child_switch_backend(service: Any, member: SwitchGroupMember) -> SwitchBackend:
    """Instantiate one concrete child switch backend from the shared registry."""
    if member.backend_type == "switch_group":
        raise ValueError("Switch group members may not themselves be switch_group backends")
    from .registry import SWITCH_BACKENDS

    constructor = SWITCH_BACKENDS.get(member.backend_type)
    if constructor is None:
        raise ValueError(f"Unsupported switch-group child backend '{member.backend_type}'")
    return cast(SwitchBackend, constructor(service, config_path=str(member.config_path)))


def _validated_member_capabilities(
    member: SwitchGroupMember,
    backend: SwitchBackend,
) -> SwitchCapabilities:
    """Return child capabilities after validating that the member is a one-phase switch."""
    capabilities = backend.capabilities()
    if tuple(capabilities.supported_phase_selections) != ("P1",):
        raise ValueError(
            f"Switch group member {member.phase_label} must expose single-phase support only"
        )
    return capabilities


def _aggregated_switching_mode(capabilities: Mapping[str, SwitchCapabilities]) -> SwitchingMode:
    """Return the effective switching mode across all configured child members."""
    if any(cap.switching_mode == "contactor" for cap in capabilities.values()):
        return "contactor"
    return "direct"


def _aggregated_requires_charge_pause(capabilities: Mapping[str, SwitchCapabilities]) -> bool:
    """Return whether any child member requires a paused charge for phase changes."""
    return any(bool(cap.requires_charge_pause_for_phase_change) for cap in capabilities.values())


def _aggregated_max_direct_switch_power_w(
    capabilities: Mapping[str, SwitchCapabilities],
    switching_mode: SwitchingMode,
) -> float | None:
    """Return the most conservative direct-switch power limit across child members."""
    if switching_mode == "contactor":
        return None
    limits = _direct_switch_power_limits(capabilities)
    return min(limits) if limits else None


def _direct_switch_power_limits(capabilities: Mapping[str, SwitchCapabilities]) -> list[float]:
    """Return explicit direct-switch power limits from all child capabilities."""
    return [
        float(limit)
        for limit in (cap.max_direct_switch_power_w for cap in capabilities.values())
        if limit is not None
    ]


def load_switch_group_settings(service: Any, config_path: str = "") -> SwitchGroupSettings:
    """Return normalized switch-group settings, including aggregated child capabilities."""
    normalized_path = str(config_path).strip()
    if not normalized_path:
        raise ValueError("Switch group backend requires a config path")
    parser = _config(normalized_path)
    capabilities = config_section(parser, "Capabilities")
    members = config_section(parser, "Members")
    phase_members = _phase_members(normalized_path, members)
    child_backends = {
        label: _child_switch_backend(service, member)
        for label, member in phase_members.items()
    }
    child_capabilities = {
        label: _validated_member_capabilities(phase_members[label], backend)
        for label, backend in child_backends.items()
    }
    supported_phase_selections = _supported_phase_selections(capabilities, phase_members)
    switching_mode = _aggregated_switching_mode(child_capabilities)
    return SwitchGroupSettings(
        phase_members=phase_members,
        phase_switch_targets=_phase_switch_targets(supported_phase_selections),
        supported_phase_selections=supported_phase_selections,
        switching_mode=switching_mode,
        requires_charge_pause_for_phase_change=_aggregated_requires_charge_pause(child_capabilities),
        max_direct_switch_power_w=_aggregated_max_direct_switch_power_w(child_capabilities, switching_mode),
    )


class SwitchGroupBackend:
    """Coordinate a set of single-phase child switch backends as one logical switch."""

    def __init__(self, service: Any, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_switch_group_settings(service, self.config_path)
        self._members: dict[str, SwitchBackend] = {
            label: _child_switch_backend(service, member)
            for label, member in self.settings.phase_members.items()
        }
        self._selected_phase_selection: PhaseSelection = self.settings.supported_phase_selections[0]

    def _phase_labels(self) -> tuple[str, ...]:
        """Return the configured phase labels in deterministic physical order."""
        return tuple(label for label in _PHASE_LABELS if label in self._members)

    def _labels_for_selection(self, selection: PhaseSelection) -> tuple[str, ...]:
        """Return the child labels energized by one logical phase selection."""
        return self.settings.phase_switch_targets[selection]

    def _phase_selection_from_active_labels(self, active_labels: frozenset[str]) -> PhaseSelection:
        """Infer one normalized phase selection from the currently active child members."""
        if not active_labels:
            return self._selected_phase_selection
        for selection in reversed(self.settings.supported_phase_selections):
            if frozenset(self._labels_for_selection(selection)) == active_labels:
                return selection
        return self._selected_phase_selection

    @staticmethod
    def _aggregate_feedback_closed(
        states: Mapping[str, SwitchState],
        active_labels: frozenset[str],
    ) -> bool | None:
        """Return one conservative group-level feedback flag when child feedback is explicit."""
        explicit = SwitchGroupBackend._explicit_feedback_states(states)
        if not explicit:
            return None
        for label, feedback_closed in explicit.items():
            expected_closed = label in active_labels
            if feedback_closed != expected_closed:
                return False
        if len(explicit) != len(states):
            return None
        return bool(active_labels)

    @staticmethod
    def _explicit_feedback_states(states: Mapping[str, SwitchState]) -> dict[str, bool]:
        """Return explicit child feedback states only."""
        return {
            label: bool(state.feedback_closed)
            for label, state in states.items()
            if state.feedback_closed is not None
        }

    @staticmethod
    def _aggregate_interlock_ok(states: Mapping[str, SwitchState]) -> bool | None:
        """Return one conservative group-level interlock flag across child members."""
        explicit = SwitchGroupBackend._explicit_interlock_states(states)
        if not explicit:
            return None
        if not all(explicit):
            return False
        if len(explicit) != len(states):
            return None
        return True

    @staticmethod
    def _explicit_interlock_states(states: Mapping[str, SwitchState]) -> list[bool]:
        """Return explicit child interlock states only."""
        return [
            bool(state.interlock_ok)
            for state in states.values()
            if state.interlock_ok is not None
        ]

    def capabilities(self) -> SwitchCapabilities:
        """Return the aggregated logical switch capabilities."""
        return SwitchCapabilities(
            switching_mode=self.settings.switching_mode,
            supported_phase_selections=self.settings.supported_phase_selections,
            requires_charge_pause_for_phase_change=self.settings.requires_charge_pause_for_phase_change,
            max_direct_switch_power_w=self.settings.max_direct_switch_power_w,
        )

    def read_switch_state(self) -> SwitchState:
        """Return one logical switch state aggregated from the child members."""
        states = {
            label: self._members[label].read_switch_state()
            for label in self._phase_labels()
        }
        active_labels = frozenset(label for label, state in states.items() if state.enabled)
        return SwitchState(
            enabled=bool(active_labels),
            phase_selection=self._phase_selection_from_active_labels(active_labels),
            feedback_closed=self._aggregate_feedback_closed(states, active_labels),
            interlock_ok=self._aggregate_interlock_ok(states),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Apply one logical switch command across all configured child members."""
        desired_labels = frozenset(self._labels_for_selection(self._selected_phase_selection)) if enabled else frozenset()
        for label in self._phase_labels():
            self._members[label].set_enabled(label in desired_labels)

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Store one validated logical phase selection for the next enable cycle."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for switch group backend")
        self._selected_phase_selection = selection
