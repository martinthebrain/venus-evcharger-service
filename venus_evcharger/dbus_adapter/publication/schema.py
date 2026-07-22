# SPDX-License-Identifier: GPL-3.0-or-later
"""Concrete Venus paths and formatting for semantic publication fields."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH, venus_path_writeable
from venus_evcharger.ports.gateway_publication import CompanionServiceKind

DbusFormatter = Callable[[str, object], str]


@dataclass(frozen=True, slots=True)
class PublicationPathSpec:
    """One adapter-owned mapping from a semantic field to a Venus path."""

    path: str
    default: object
    writeable: bool = False
    formatter: DbusFormatter | None = None


def _number(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def format_kwh(_path: str, value: object) -> str:
    return f"{_number(value):.2f} kWh"


def format_amps(_path: str, value: object) -> str:
    return f"{_number(value):.1f} A"


def format_watts(_path: str, value: object) -> str:
    return f"{_number(value):.1f} W"


def format_volts(_path: str, value: object) -> str:
    return f"{_number(value):.1f} V"


def format_status(_path: str, value: object) -> str:
    labels = {
        0: "Getrennt",
        1: "Bereit",
        2: "Laden",
        3: "Fertig",
        4: "Warten auf PV",
        6: "Warten auf Start",
    }
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return "Unbekannt"
    try:
        return labels.get(int(value), "Unbekannt")
    except ValueError:
        return "Unbekannt"


_ENERGY_FIELDS = frozenset(
    (
        "energy_forward_kwh",
        "l1_energy_forward_kwh",
        "l2_energy_forward_kwh",
        "l3_energy_forward_kwh",
    )
)
_CURRENT_FIELDS = frozenset(
    (
        "ac_current_a",
        "charge_current_a",
        "l1_current_a",
        "l2_current_a",
        "l3_current_a",
        "min_current",
        "max_current",
        "set_current",
    )
)
_POWER_FIELDS = frozenset(
    (
        "ac_power_w",
        "l1_power_w",
        "l2_power_w",
        "l3_power_w",
    )
)
_VOLTAGE_FIELDS = frozenset(
    (
        "ac_voltage_v",
        "l1_voltage_v",
        "l2_voltage_v",
        "l3_voltage_v",
    )
)
_TEXT_SUFFIXES = (
    "_state",
    "_reason",
    "_source",
    "_detail",
    "_profile",
    "_mode",
    "_path",
    "_version",
    "_date",
    "_day",
    "_start",
    "_until",
    "_target",
    "_current",
    "_observed",
    "_candidate",
    "_backend",
    "_error",
)
_STRING_FIELDS = frozenset(
    (
        "auto_health",
        "auto_status_source",
        "phase_selection",
        "phase_selection_active",
        "supported_phase_selections",
        "auto_scheduled_enabled_days",
        "auto_scheduled_latest_end_time",
        "auto_phase_supported_configured",
        "auto_phase_supported_effective",
        "auto_runtime_overrides_path",
    )
)
_NEGATIVE_SENTINEL_MARKERS = ("_age", "_remaining", "_threshold", "_surplus", "_soc_percent")
_FORMATTERS_BY_FIELD: Mapping[str, DbusFormatter] = {
    **dict.fromkeys(_ENERGY_FIELDS, format_kwh),
    **dict.fromkeys(_CURRENT_FIELDS, format_amps),
    **dict.fromkeys(_POWER_FIELDS, format_watts),
    **dict.fromkeys(_VOLTAGE_FIELDS, format_volts),
    "status": format_status,
}


def _formatter_for(field: str) -> DbusFormatter | None:
    return _FORMATTERS_BY_FIELD.get(field)


def _default_for(field: str) -> object:
    if field in _STRING_FIELDS or field.endswith(_TEXT_SUFFIXES):
        return ""
    if any(marker in field for marker in _NEGATIVE_SENTINEL_MARKERS):
        return -1
    return 0


EVCS_PUBLICATION_SPECS = {
    field: PublicationPathSpec(
        path=path,
        default=_default_for(field),
        writeable=venus_path_writeable(path),
        formatter=_formatter_for(field),
    )
    for field, path in EVCS_FIELD_TO_PATH.items()
    if field != "update_index"
}

COMPANION_PUBLICATION_SPECS: Mapping[CompanionServiceKind, Mapping[str, PublicationPathSpec]] = {
    "battery": {
        "connected": PublicationPathSpec("/Connected", 0),
        "soc_percent": PublicationPathSpec("/Soc", None),
        "dc_power_w": PublicationPathSpec("/Dc/0/Power", 0.0, formatter=format_watts),
        "capacity_wh": PublicationPathSpec("/Capacity", None),
    },
    "grid": {
        "connected": PublicationPathSpec("/Connected", 0),
        "ac_power_w": PublicationPathSpec("/Ac/Power", 0.0, formatter=format_watts),
        "l1_power_w": PublicationPathSpec("/Ac/L1/Power", 0.0, formatter=format_watts),
        "l2_power_w": PublicationPathSpec("/Ac/L2/Power", 0.0, formatter=format_watts),
        "l3_power_w": PublicationPathSpec("/Ac/L3/Power", 0.0, formatter=format_watts),
    },
    "pv_inverter": {
        "connected": PublicationPathSpec("/Connected", 0),
        "ac_power_w": PublicationPathSpec("/Ac/Power", 0.0, formatter=format_watts),
        "l1_power_w": PublicationPathSpec("/Ac/L1/Power", 0.0, formatter=format_watts),
        "l2_power_w": PublicationPathSpec("/Ac/L2/Power", 0.0, formatter=format_watts),
        "l3_power_w": PublicationPathSpec("/Ac/L3/Power", 0.0, formatter=format_watts),
    },
}


def validate_fields(
    fields: Mapping[str, object],
    specs: Mapping[str, PublicationPathSpec],
    *,
    surface: str,
) -> dict[str, object]:
    """Validate semantic field names before they reach concrete DBus paths."""
    unknown = sorted(set(fields) - set(specs))
    if unknown:
        raise ValueError(f"Unknown {surface} publication fields: {', '.join(unknown)}")
    return {str(field): value for field, value in fields.items()}


__all__ = [
    "COMPANION_PUBLICATION_SPECS",
    "EVCS_PUBLICATION_SPECS",
    "PublicationPathSpec",
    "format_amps",
    "format_kwh",
    "format_status",
    "format_volts",
    "format_watts",
    "validate_fields",
]
