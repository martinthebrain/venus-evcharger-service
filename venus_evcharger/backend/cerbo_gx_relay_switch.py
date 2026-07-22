# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron GX/Cerbo relay-backed normalized switch backend."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from typing import Literal

from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag
from venus_evcharger.ports.gateway_operations import (
    GatewayOperationsPort,
    GxRelaySetRequest,
    require_gateway_operations,
)

from .config_file import config_section
from .models import PhaseSelection, SwitchCapabilities, SwitchState, normalize_phase_selection_tuple

ContactMode = Literal["NO", "NC"]


@dataclass(frozen=True)
class CerboGxRelaySwitchSettings:
    """Normalized settings for one local Victron GX relay actuator."""

    relay_index: int
    contact_mode: ContactMode
    ensure_manual_function: bool
    verify_settle_seconds: float
    verify_retry_seconds: float
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool


def _config(path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    setattr(parser, "optionxform", str)
    if not str(path).strip():
        return parser
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(path)
    return parser


def _relay_index(value: object) -> int:
    try:
        index = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Cerbo GX relay backend requires RelayIndex 0 or 1") from exc
    if index not in (0, 1):
        raise ValueError("Cerbo GX relay backend supports RelayIndex 0 or 1")
    return index


def _relay_index_or_default(value: object) -> int:
    if value is None or not str(value).strip():
        return 0
    return _relay_index(value)


def _contact_mode(value: object) -> ContactMode:
    mode = str(value).strip().upper()
    if mode in {"NO", "NORMALLY_OPEN", "NORMALLY-OPEN"}:
        return "NO"
    if mode in {"NC", "NORMALLY_CLOSED", "NORMALLY-CLOSED"}:
        return "NC"
    raise ValueError("Cerbo GX relay backend requires ContactMode NO or NC")


def _contact_mode_or_default(value: object) -> ContactMode:
    if value is None or not str(value).strip():
        return "NO"
    return _contact_mode(value)


def _binary_flag_or_default(value: object, default: bool) -> bool:
    if value is None:
        return default
    return bool(normalize_binary_flag(value))


def _positive_seconds(value: object, default: float) -> float:
    seconds = finite_float_or_none(value)
    if seconds is None or seconds < 0.0:
        return default
    return float(seconds)


def load_cerbo_gx_relay_switch_settings(config_path: str) -> CerboGxRelaySwitchSettings:
    """Return normalized Cerbo GX relay switch settings."""
    parser = _config(config_path)
    adapter = config_section(parser, "Adapter")
    capabilities = config_section(parser, "Capabilities")
    return CerboGxRelaySwitchSettings(
        relay_index=_relay_index_or_default(adapter.get("RelayIndex")),
        contact_mode=_contact_mode_or_default(adapter.get("ContactMode")),
        ensure_manual_function=_binary_flag_or_default(adapter.get("EnsureManualFunction"), True),
        verify_settle_seconds=_positive_seconds(adapter.get("VerifySettleSeconds"), 0.1),
        verify_retry_seconds=_positive_seconds(adapter.get("VerifyRetrySeconds"), 0.2),
        supported_phase_selections=normalize_phase_selection_tuple(
            capabilities.get("SupportedPhaseSelections"), ("P1",)
        ),
        requires_charge_pause_for_phase_change=_binary_flag_or_default(
            capabilities.get("RequiresChargePauseForPhaseChange"),
            False,
        ),
    )


class CerboGxRelaySwitchBackend:
    """Control one GX relay through the semantic system gateway."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_cerbo_gx_relay_switch_settings(self.config_path)
        self._gateway: GatewayOperationsPort = require_gateway_operations(service)
        default_selection = self.settings.supported_phase_selections[0]
        requested_selection = getattr(service, "requested_phase_selection", None)
        self._selected_phase_selection: PhaseSelection = (
            requested_selection
            if requested_selection in self.settings.supported_phase_selections
            else default_selection
        )

    def capabilities(self) -> SwitchCapabilities:
        """Return the configured relay capabilities."""
        return SwitchCapabilities(
            switching_mode="contactor",
            supported_phase_selections=self.settings.supported_phase_selections,
            requires_charge_pause_for_phase_change=self.settings.requires_charge_pause_for_phase_change,
            max_direct_switch_power_w=None,
        )

    def read_switch_state(self) -> SwitchState:
        """Read one normalized switch state from the semantic gateway cache."""
        relay_state = self._gateway.read_gx_relay_state(
            self.settings.relay_index,
            max_age_seconds=max(1.0, self.settings.verify_settle_seconds + self.settings.verify_retry_seconds),
        )
        enabled = self._enabled_from_relay_state(relay_state)
        return SwitchState(
            enabled=enabled,
            phase_selection=self._selected_phase_selection,
        )

    def set_enabled(self, enabled: bool) -> None:
        """Submit one verified GX relay operation to the system gateway."""
        receipt = self._gateway.set_gx_relay_enabled(
            GxRelaySetRequest(
                relay_index=self.settings.relay_index,
                contact_mode=self.settings.contact_mode,
                enabled=bool(enabled),
                ensure_manual=self.settings.ensure_manual_function,
                verify_settle_seconds=self.settings.verify_settle_seconds,
                verify_retry_seconds=self.settings.verify_retry_seconds,
            )
        )
        if not receipt.accepted:
            raise RuntimeError(f"GX relay {self.settings.relay_index} operation was rejected")

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Store one supported phase selection for API consistency."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for Cerbo GX relay backend")
        self._selected_phase_selection = selection

    def _relay_state_for_enabled(self, enabled: bool) -> int:
        if self.settings.contact_mode == "NC":
            return 0 if enabled else 1
        return 1 if enabled else 0

    def _enabled_from_relay_state(self, relay_state: int | None) -> bool:
        if relay_state is None:
            return False
        return relay_state == self._relay_state_for_enabled(True)
