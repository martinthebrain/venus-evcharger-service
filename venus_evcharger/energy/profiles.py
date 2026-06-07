# SPDX-License-Identifier: GPL-3.0-or-later
"""Named energy-source profile defaults for common external-source setups."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class EnergySourceProfile:
    """Default connector and path settings for one named energy-source profile."""

    profile_name: str
    role: str
    connector_type: str
    vendor_name: str = ""
    platform: str = ""
    family_name: str = ""
    access_mode: str = ""
    firmware_class: str = ""
    service_prefix: str = ""
    soc_path: str = ""
    battery_power_path: str = ""
    ac_power_path: str = ""
    pv_power_path: str = ""
    grid_interaction_path: str = ""
    operating_mode_path: str = ""
    battery_chemistry: str = "lfp"
    capacity_auto_estimate: bool = True
    capacity_wh_path: str = ""
    capacity_ah_path: str = "/InstalledCapacity"
    voltage_path: str = "/Dc/0/Voltage"
    capacity_estimate_min_soc: float = 95.0
    capacity_startup_recheck_seconds: float = 300.0
    default_host: str = ""
    default_port_candidates: tuple[int, ...] = ()
    default_unit_id_candidates: tuple[int, ...] = ()
    read_support: str = "supported"
    write_support: str = "unsupported"
    probe_required: bool = False
    idle_unreachable_policy: str = "strict"


def _huawei_profile(
    profile_name: str,
    *,
    platform: str,
    access_mode: str,
    firmware_class: str,
    family_name: str = "",
    default_host: str = "",
    default_port_candidates: tuple[int, ...] = (),
    default_unit_id_candidates: tuple[int, ...] = (),
) -> EnergySourceProfile:
    return EnergySourceProfile(
        profile_name=profile_name,
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform=platform,
        family_name=family_name or platform,
        access_mode=access_mode,
        firmware_class=firmware_class,
        default_host=default_host,
        default_port_candidates=default_port_candidates,
        default_unit_id_candidates=default_unit_id_candidates,
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    )


def _profile_variant(base: EnergySourceProfile, profile_name: str, *, family_name: str) -> EnergySourceProfile:
    return replace(base, profile_name=profile_name, family_name=family_name)


_PROFILES = {
    "dbus-battery": EnergySourceProfile(
        profile_name="dbus-battery",
        role="battery",
        connector_type="dbus",
        service_prefix="com.victronenergy.battery",
        soc_path="/Soc",
        battery_power_path="/Dc/0/Power",
    ),
    "dbus-hybrid": EnergySourceProfile(
        profile_name="dbus-hybrid",
        role="hybrid-inverter",
        connector_type="dbus",
        soc_path="/Soc",
        battery_power_path="/Dc/0/Power",
        ac_power_path="/Ac/Power",
        pv_power_path="/Pv/Power",
        grid_interaction_path="/Grid/Power",
        operating_mode_path="/Mode",
    ),
    "template-http-hybrid": EnergySourceProfile(
        profile_name="template-http-hybrid",
        role="hybrid-inverter",
        connector_type="template_http",
    ),
    "modbus-hybrid": EnergySourceProfile(
        profile_name="modbus-hybrid",
        role="hybrid-inverter",
        connector_type="modbus",
    ),
    "command-json-hybrid": EnergySourceProfile(
        profile_name="command-json-hybrid",
        role="hybrid-inverter",
        connector_type="command_json",
    ),
    "opendtu-pvinverter": EnergySourceProfile(
        profile_name="opendtu-pvinverter",
        role="inverter",
        connector_type="opendtu_http",
        vendor_name="OpenDTU",
        platform="OpenDTU",
        family_name="OpenDTU",
        access_mode="http_json",
        firmware_class="status_api",
        read_support="supported",
        write_support="unsupported",
        probe_required=False,
        idle_unreachable_policy="allow_plausible_idle",
    ),
    "huawei_ma_native_ap": _huawei_profile(
        "huawei_ma_native_ap",
        platform="MA",
        family_name="MA",
        access_mode="native_ap",
        firmware_class="local_ap_6607",
        default_host="192.168.200.1",
        default_port_candidates=(6607, 502),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_ma_native_lan": _huawei_profile(
        "huawei_ma_native_lan",
        platform="MA",
        family_name="MA",
        access_mode="native_lan",
        firmware_class="legacy_lan_open",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_ma_sdongle": _huawei_profile(
        "huawei_ma_sdongle",
        platform="MA",
        family_name="MA",
        access_mode="sdongle",
        firmware_class="sdongle_third_party",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_ma_smartlogger_modbus_tcp": _huawei_profile(
        "huawei_ma_smartlogger_modbus_tcp",
        platform="MA",
        family_name="MA",
        access_mode="smartlogger",
        firmware_class="smartlogger_502",
        default_port_candidates=(502,),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_mb_native_ap": _huawei_profile(
        "huawei_mb_native_ap",
        platform="MB",
        family_name="MB",
        access_mode="native_ap",
        firmware_class="local_ap_6607",
        default_host="192.168.200.1",
        default_port_candidates=(6607, 502),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_mb_native_lan": _huawei_profile(
        "huawei_mb_native_lan",
        platform="MB",
        family_name="MB",
        access_mode="native_lan",
        firmware_class="legacy_lan_open",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_mb_sdongle": _huawei_profile(
        "huawei_mb_sdongle",
        platform="MB",
        family_name="MB",
        access_mode="sdongle",
        firmware_class="sdongle_third_party",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_mb_smartlogger_modbus_tcp": _huawei_profile(
        "huawei_mb_smartlogger_modbus_tcp",
        platform="MB",
        family_name="MB",
        access_mode="smartlogger",
        firmware_class="smartlogger_502",
        default_port_candidates=(502,),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_mb_unit1": _huawei_profile(
        "huawei_mb_unit1",
        platform="MB",
        family_name="MB",
        access_mode="unit_split",
        firmware_class="mb_unit_split",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_mb_unit2": _huawei_profile(
        "huawei_mb_unit2",
        platform="MB",
        family_name="MB",
        access_mode="unit_split",
        firmware_class="mb_unit_split",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
    ),
    "huawei_smartlogger_modbus_tcp": _huawei_profile(
        "huawei_smartlogger_modbus_tcp",
        platform="smartlogger",
        family_name="generic",
        access_mode="smartlogger",
        firmware_class="smartlogger_502",
        default_port_candidates=(502,),
        default_unit_id_candidates=(0, 1),
    ),
}

for _family in ("L1", "LC0", "LB0", "M1"):
    _family_key = _family.lower()
    _PROFILES[f"huawei_{_family_key}_native_ap"] = _profile_variant(
        _PROFILES["huawei_ma_native_ap"],
        f"huawei_{_family_key}_native_ap",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_native_lan"] = _profile_variant(
        _PROFILES["huawei_ma_native_lan"],
        f"huawei_{_family_key}_native_lan",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_sdongle"] = _profile_variant(
        _PROFILES["huawei_ma_sdongle"],
        f"huawei_{_family_key}_sdongle",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_smartlogger_modbus_tcp"] = _profile_variant(
        _PROFILES["huawei_ma_smartlogger_modbus_tcp"],
        f"huawei_{_family_key}_smartlogger_modbus_tcp",
        family_name=_family,
    )

for _family in ("MAP0", "MB0"):
    _family_key = _family.lower()
    _PROFILES[f"huawei_{_family_key}_native_ap"] = _profile_variant(
        _PROFILES["huawei_mb_native_ap"],
        f"huawei_{_family_key}_native_ap",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_native_lan"] = _profile_variant(
        _PROFILES["huawei_mb_native_lan"],
        f"huawei_{_family_key}_native_lan",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_sdongle"] = _profile_variant(
        _PROFILES["huawei_mb_sdongle"],
        f"huawei_{_family_key}_sdongle",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_smartlogger_modbus_tcp"] = _profile_variant(
        _PROFILES["huawei_mb_smartlogger_modbus_tcp"],
        f"huawei_{_family_key}_smartlogger_modbus_tcp",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_unit1"] = _profile_variant(
        _PROFILES["huawei_mb_unit1"],
        f"huawei_{_family_key}_unit1",
        family_name=_family,
    )
    _PROFILES[f"huawei_{_family_key}_unit2"] = _profile_variant(
        _PROFILES["huawei_mb_unit2"],
        f"huawei_{_family_key}_unit2",
        family_name=_family,
    )

_ALIASES = {
    "battery": "dbus-battery",
    "hybrid": "dbus-hybrid",
    "template-http": "template-http-hybrid",
    "http-hybrid": "template-http-hybrid",
    "modbus": "modbus-hybrid",
    "command-json": "command-json-hybrid",
    "helper": "command-json-hybrid",
    "opendtu": "opendtu-pvinverter",
    "opendtu-inverter": "opendtu-pvinverter",
    "growatt-opendtu": "opendtu-pvinverter",
    "huawei_sun5000_lb0_native_ap": "huawei_lb0_native_ap",
    "huawei_sun5000_lb0_native_lan": "huawei_lb0_native_lan",
    "huawei_sun5000_lb0_sdongle": "huawei_lb0_sdongle",
    "huawei_sun5000_lb0_smartlogger_modbus_tcp": "huawei_lb0_smartlogger_modbus_tcp",
    "huawei_sun5000_map0_native_ap": "huawei_map0_native_ap",
    "huawei_sun5000_map0_native_lan": "huawei_map0_native_lan",
    "huawei_sun5000_map0_sdongle": "huawei_map0_sdongle",
    "huawei_sun5000_map0_smartlogger_modbus_tcp": "huawei_map0_smartlogger_modbus_tcp",
    "huawei_sun5000_map0_unit1": "huawei_map0_unit1",
    "huawei_sun5000_map0_unit2": "huawei_map0_unit2",
}


def available_energy_source_profiles() -> tuple[str, ...]:
    """Return canonical profile names in stable display order."""
    return tuple(_PROFILES)


def resolve_energy_source_profile(profile_name: object) -> EnergySourceProfile | None:
    """Return the normalized profile for a configured profile name or alias."""
    normalized = str(profile_name).strip().lower()
    if not normalized:
        return None
    canonical_name = _ALIASES.get(normalized, normalized)
    return _PROFILES.get(canonical_name)


def energy_source_profile_defaults(profile_name: object) -> Mapping[str, Any]:
    """Expose one profile as a mapping suitable for config overlay logic."""
    profile = resolve_energy_source_profile(profile_name)
    if profile is None:
        return {}
    return {
        "Profile": profile.profile_name,
        "Role": profile.role,
        "Type": profile.connector_type,
        "ServicePrefix": profile.service_prefix,
        "SocPath": profile.soc_path,
        "BatteryPowerPath": profile.battery_power_path,
        "AcPowerPath": profile.ac_power_path,
        "PvPowerPath": profile.pv_power_path,
        "GridInteractionPath": profile.grid_interaction_path,
        "OperatingModePath": profile.operating_mode_path,
        "BatteryChemistry": profile.battery_chemistry,
        "CapacityAutoEstimate": profile.capacity_auto_estimate,
        "CapacityWhPath": profile.capacity_wh_path,
        "CapacityAhPath": profile.capacity_ah_path,
        "VoltagePath": profile.voltage_path,
        "CapacityEstimateMinSoc": profile.capacity_estimate_min_soc,
        "CapacityStartupRecheckSeconds": profile.capacity_startup_recheck_seconds,
    }


def energy_source_profile_details(profile_name: object) -> Mapping[str, Any]:
    """Return stable outward-facing metadata for one configured profile."""
    profile = resolve_energy_source_profile(profile_name)
    if profile is None:
        return {}
    return {
        "profile_name": profile.profile_name,
        "vendor_name": profile.vendor_name,
        "platform": profile.platform,
        "family_name": profile.family_name,
        "access_mode": profile.access_mode,
        "firmware_class": profile.firmware_class,
        "connector_type": profile.connector_type,
        "role": profile.role,
        "default_host": profile.default_host,
        "default_port_candidates": list(profile.default_port_candidates),
        "default_unit_id_candidates": list(profile.default_unit_id_candidates),
        "read_support": profile.read_support,
        "write_support": profile.write_support,
        "probe_required": profile.probe_required,
        "idle_unreachable_policy": profile.idle_unreachable_policy,
    }


def energy_source_profile_probe_plan(
    profile_name: object,
    *,
    configured_host: object = "",
    configured_port: object = None,
    configured_unit_id: object = None,
) -> Mapping[str, Any]:
    """Return an effective probe plan for profiles that need field validation."""
    profile = resolve_energy_source_profile(profile_name)
    if profile is None:
        return {}
    host = str(configured_host).strip() or profile.default_host
    ports = _candidate_values(configured_port, profile.default_port_candidates)
    unit_ids = _candidate_values(configured_unit_id, profile.default_unit_id_candidates)
    return {
        "profile_name": profile.profile_name,
        "connector_type": profile.connector_type,
        "host": host,
        "port_candidates": ports,
        "unit_id_candidates": unit_ids,
        "probe_required": profile.probe_required,
    }


def _candidate_values(configured_value: object, defaults: tuple[int, ...]) -> list[int]:
    parsed_value = _candidate_int_value(configured_value)
    if parsed_value is not None:
        return [parsed_value]
    return list(defaults)


def _candidate_int_value(configured_value: object) -> int | None:
    if isinstance(configured_value, bool):
        return None
    if isinstance(configured_value, int):
        return int(configured_value)
    if isinstance(configured_value, str):
        return _candidate_int_from_string(configured_value)
    return None


def _candidate_int_from_string(configured_value: str) -> int | None:
    stripped_value = configured_value.strip()
    if not stripped_value:
        return None
    try:
        return int(stripped_value)
    except ValueError:
        return None
