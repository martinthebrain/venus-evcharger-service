# SPDX-License-Identifier: GPL-3.0-or-later
"""Load normalized runtime backend summaries from wallbox configuration."""

from __future__ import annotations

import configparser

from venus_evcharger.topology.config import parse_topology_config, validate_topology_config

from .config_migration import migrate_legacy_backend_config
from .config_normalization import _validate_legacy_backend_values
from .config_summary import _build_runtime_summary
from .config_topology import _runtime_summary_from_topology
from .models import BackendRuntimeSummary


def topology_sections_present(config: configparser.ConfigParser) -> bool:
    """Return whether one config includes normalized topology sections."""
    return config.has_section("Topology")


def _runtime_summary_from_legacy_config(config: configparser.ConfigParser) -> BackendRuntimeSummary:
    """Migrate historical INI fields once, then build the canonical runtime model."""
    migrated = migrate_legacy_backend_config(config)
    _validate_legacy_backend_values(
        migrated.backend_mode,
        "none" if migrated.meter_type is None else migrated.meter_type,
        "none" if migrated.switch_type is None else migrated.switch_type,
        migrated.charger_type,
    )
    validate_topology_config(migrated.topology)
    return _build_runtime_summary(
        backend_mode=migrated.backend_mode,
        meter_type=migrated.meter_type,
        meter_config_path=migrated.meter_config_path,
        switch_type=migrated.switch_type,
        switch_config_path=migrated.switch_config_path,
        charger_type=migrated.charger_type,
        charger_config_path=migrated.charger_config_path,
        legacy_host=migrated.host,
    )


def load_runtime_backend_summary(config: configparser.ConfigParser) -> BackendRuntimeSummary:
    """Return one normalized runtime backend summary from wallbox config."""
    if topology_sections_present(config):
        return _runtime_summary_from_topology(parse_topology_config(config))
    return _runtime_summary_from_legacy_config(config)
