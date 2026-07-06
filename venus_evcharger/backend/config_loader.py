# SPDX-License-Identifier: GPL-3.0-or-later
"""Load normalized runtime backend summaries from wallbox configuration."""

from __future__ import annotations

import configparser

from venus_evcharger.topology.config import _legacy_runtime_values, parse_topology_config

from .config_normalization import (
    _runtime_meter_role_from_legacy,
    _runtime_switch_role_from_legacy,
    _validate_legacy_backend_values,
    normalize_backend_mode,
    normalize_config_path,
    normalize_optional_backend_type,
)
from .config_summary import _build_runtime_summary
from .config_topology import _runtime_summary_from_topology
from .models import BackendRuntimeSummary


def topology_sections_present(config: configparser.ConfigParser) -> bool:
    """Return whether one config includes normalized topology sections."""
    return config.has_section("Topology")


def _backends_section(config: configparser.ConfigParser) -> configparser.SectionProxy:
    """Return the preferred legacy config section for backend settings."""
    return config["Backends"] if config.has_section("Backends") else config["DEFAULT"]


def _runtime_summary_from_legacy_config(config: configparser.ConfigParser) -> BackendRuntimeSummary:
    """Return one runtime backend summary from legacy combined/split config sections."""
    section = _backends_section(config)
    legacy = _legacy_runtime_values(config)
    mode = normalize_backend_mode(section.get("Mode"))
    raw_meter_type = legacy.meter_type
    raw_switch_type = legacy.switch_type
    charger_type = normalize_optional_backend_type(legacy.charger_type_raw)
    _validate_legacy_backend_values(mode, raw_meter_type, raw_switch_type, charger_type)
    return _build_runtime_summary(
        backend_mode=mode,
        meter_type=_runtime_meter_role_from_legacy(mode, raw_meter_type),
        meter_config_path=normalize_config_path(legacy.meter_path),
        switch_type=_runtime_switch_role_from_legacy(mode, raw_switch_type),
        switch_config_path=normalize_config_path(legacy.switch_path),
        charger_type=charger_type,
        charger_config_path=normalize_config_path(legacy.charger_path),
        legacy_host=legacy.host,
    )


def load_runtime_backend_summary(config: configparser.ConfigParser) -> BackendRuntimeSummary:
    """Return one normalized runtime backend summary from wallbox config."""
    if topology_sections_present(config):
        return _runtime_summary_from_topology(parse_topology_config(config))
    return _runtime_summary_from_legacy_config(config)
