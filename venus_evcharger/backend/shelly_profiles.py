# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly family preset descriptors shared by multiple backends."""

from __future__ import annotations

from dataclasses import dataclass

from .config_file import normalized_optional_lower_text
from .models import PhaseSelection


@dataclass(frozen=True)
class ShellyProfileDefaults:
    """One config-selectable Shelly family preset."""

    component: str
    device_id: int
    roles: tuple[str, ...]
    default_phase_selection: PhaseSelection | None = None


_SHELLY_PROFILES: dict[str, ShellyProfileDefaults] = {
    "switch_1ch": ShellyProfileDefaults(component="Switch", device_id=0, roles=("switch",)),
    "switch_1ch_with_pm": ShellyProfileDefaults(component="Switch", device_id=0, roles=("switch", "meter")),
    "switch_multi_or_plug": ShellyProfileDefaults(component="Switch", device_id=0, roles=("switch", "meter")),
    "switch_or_cover_profile": ShellyProfileDefaults(component="Switch", device_id=0, roles=("switch",)),
    "pm1_meter_only": ShellyProfileDefaults(component="PM1", device_id=0, roles=("meter",), default_phase_selection="P1"),
    "pm1_meter": ShellyProfileDefaults(component="PM1", device_id=0, roles=("meter",), default_phase_selection="P1"),
    "em1_meter_single_or_dual": ShellyProfileDefaults(component="EM1", device_id=0, roles=("meter",), default_phase_selection="P1"),
    "em1_meter": ShellyProfileDefaults(component="EM1", device_id=0, roles=("meter",), default_phase_selection="P1"),
    "em_3phase_profiled": ShellyProfileDefaults(
        component="EM",
        device_id=0,
        roles=("meter",),
        default_phase_selection="P1_P2_P3",
    ),
    "em_meter": ShellyProfileDefaults(component="EM", device_id=0, roles=("meter",), default_phase_selection="P1_P2_P3"),
}


def normalize_shelly_profile_name(value: object) -> str | None:
    """Return one normalized optional Shelly family preset name."""
    return normalized_optional_lower_text(value)


def resolve_shelly_profile(profile_name: str | None) -> ShellyProfileDefaults | None:
    """Return one Shelly profile descriptor when configured."""
    if profile_name is None:
        return None
    defaults = _SHELLY_PROFILES.get(profile_name)
    if defaults is None:
        supported = ",".join(sorted(_SHELLY_PROFILES))
        raise ValueError(f"Unsupported ShellyProfile '{profile_name}' (supported: {supported})")
    return defaults


def validate_shelly_profile_role(profile_name: str | None, role: str) -> None:
    """Ensure the configured Shelly profile is valid for the requested backend role."""
    defaults = resolve_shelly_profile(profile_name)
    if defaults is None or str(role).strip().lower() in defaults.roles:
        return
    supported_roles = ",".join(defaults.roles)
    raise ValueError(
        f"ShellyProfile '{profile_name}' is not valid for {role} backends (supported roles: {supported_roles})"
    )
