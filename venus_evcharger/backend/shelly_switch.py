# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly-backed normalized switch backend."""

from __future__ import annotations

from typing import Iterable

from .shelly_support import ShellyBackendBase, validate_shelly_profile_role
from .models import PhaseSelection, SwitchCapabilities, SwitchState, SwitchingMode


class ShellySwitchBackend(ShellyBackendBase):
    """Control normalized relay state through one Shelly target."""

    def __init__(
        self,
        service: object,
        config_path: str = "",
        *,
        default_switching_mode: SwitchingMode = "direct",
    ) -> None:
        super().__init__(
            service,
            config_path=config_path,
            default_switching_mode=default_switching_mode,
        )
        validate_shelly_profile_role(self.settings.profile_name, "switch")
        default_selection = self.settings.supported_phase_selections[0]
        self._selected_phase_selection: PhaseSelection = (
            self.settings.phase_selection
            if self.settings.phase_selection in self.settings.supported_phase_selections
            else default_selection
        )

    def _all_switch_ids(self) -> tuple[int, ...]:
        """Return one stable tuple of all configured Shelly relay IDs."""
        ordered: list[int] = []
        for switch_ids in self.settings.phase_switch_targets.values():
            for switch_id in switch_ids:
                if switch_id not in ordered:
                    ordered.append(int(switch_id))
        return tuple(ordered) or (int(self.settings.device_id),)

    def _switch_ids_for_selection(self, selection: PhaseSelection) -> tuple[int, ...]:
        """Return one normalized relay target tuple for the given phase selection."""
        return self.settings.phase_switch_targets.get(selection, (int(self.settings.device_id),))

    def _set_switch_ids(self, switch_ids: Iterable[int], enabled: bool) -> None:
        """Apply one boolean relay state to all given Shelly switch channels."""
        if not isinstance(enabled, bool):
            raise TypeError("Shelly switch enabled state must be bool")
        for switch_id in switch_ids:
            self._rpc_call("Switch.Set", id=int(switch_id), on=enabled)

    def _switch_outputs(self) -> dict[int, bool]:
        """Return current boolean output state for all configured relay channels."""
        outputs: dict[int, bool] = {}
        for switch_id in self._all_switch_ids():
            pm_status = self._rpc_call(f"{self.settings.component}.GetStatus", id=int(switch_id))
            outputs[int(switch_id)] = bool(pm_status["output"]) if "output" in pm_status else False
        return outputs

    def _phase_selection_from_outputs(self, active_switch_ids: frozenset[int]) -> PhaseSelection:
        """Infer one normalized phase selection from currently active relay channels."""
        if not active_switch_ids:
            return self._selected_phase_selection
        for selection in reversed(self.settings.supported_phase_selections):
            if frozenset(self._switch_ids_for_selection(selection)) == active_switch_ids:
                return selection
        return self._selected_phase_selection

    def capabilities(self) -> SwitchCapabilities:
        """Return the configured switching capabilities."""
        return SwitchCapabilities(
            switching_mode=self.settings.switching_mode,
            supported_phase_selections=self.settings.supported_phase_selections,
            requires_charge_pause_for_phase_change=self.settings.requires_charge_pause_for_phase_change,
            max_direct_switch_power_w=self.settings.max_direct_switch_power_w,
        )

    def read_switch_state(self) -> SwitchState:
        """Return one normalized switch state from Shelly output plus configured phase selection."""
        outputs = self._switch_outputs()
        active_switch_ids = frozenset(switch_id for switch_id, enabled in outputs.items() if enabled)
        return SwitchState(
            enabled=bool(active_switch_ids),
            phase_selection=self._phase_selection_from_outputs(active_switch_ids),
            feedback_closed=self._signal_readback_flag(self.settings.feedback_readback),
            interlock_ok=self._signal_readback_flag(self.settings.interlock_readback),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Switch the configured Shelly target on or off."""
        all_switch_ids = self._all_switch_ids()
        if not enabled:
            self._set_switch_ids(all_switch_ids, False)
            return
        desired_switch_ids = frozenset(self._switch_ids_for_selection(self._selected_phase_selection))
        for switch_id in all_switch_ids:
            self._rpc_call("Switch.Set", id=int(switch_id), on=switch_id in desired_switch_ids)

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Store one validated phase selection for the current backend instance."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for Shelly switch backend")
        self._selected_phase_selection = selection
