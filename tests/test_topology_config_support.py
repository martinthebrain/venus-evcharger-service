# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from venus_evcharger.backend.config import (
    backend_mode_for_service,
    backend_type_for_service,
    backend_selection_view_from_config,
    backend_selection_view,
    load_runtime_backend_summary,
    runtime_summary_from_service,
    runtime_summary_is_configured,
    runtime_summary_uses_legacy_primary_rpc,
)
from venus_evcharger.backend.config_migration import (
    _known_legacy_switch_type,
    _legacy_actuator_config,
    _legacy_charger,
    _legacy_hybrid_measurement_config,
    _legacy_measurement_config,
    _legacy_native_measurement_config,
    _legacy_policy_mode,
    _legacy_runtime_values,
    _legacy_switch_actuator_type,
    _legacy_switch_alias,
    _legacy_switch_type,
    _runtime_meter_role_from_legacy,
    _runtime_switch_role_from_legacy,
)
from venus_evcharger.backend.config_normalization import (
    _configured_text,
    _normalized_text_or_default,
    normalize_backend_type,
)
from venus_evcharger.backend.config_summary import _build_runtime_summary
from venus_evcharger.backend.config_topology import (
    _adapter_type_from_config_path,
    _adapter_type_from_parser,
    _native_meter_type_for_actuator,
    _runtime_summary_from_topology,
    _topology_backend_label,
)
from venus_evcharger.backend.models import BackendMode, BackendRuntimeSummary
from venus_evcharger.topology.config import (
    TopologyConfigError,
    _actuator_type,
    _as_bool,
    _charger_type,
    _measurement_type,
    _optional_actuator,
    _optional_charger,
    _optional_measurement,
    _optional_text,
    _policy,
    _policy_mode,
    _required_value,
    _topology_type,
    _validate_measurement,
    legacy_topology_from_config,
    parse_topology_config,
    validate_topology_config,
)
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)


def runtime_summary_fixture(
    *,
    backend_mode: BackendMode = "split",
    meter_type: str | None = "template_meter",
    switch_type: str | None = "template_switch",
    charger_type: str | None = "goe_charger",
    meter_config_path: Path | None = None,
    switch_config_path: Path | None = None,
    charger_config_path: Path | None = None,
) -> BackendRuntimeSummary:
    """Build one canonical runtime summary for service-boundary tests."""
    return BackendRuntimeSummary(
        backend_mode=backend_mode,
        meter_type=meter_type,
        meter_config_path=meter_config_path,
        switch_type=switch_type,
        switch_config_path=switch_config_path,
        charger_type=charger_type,
        charger_config_path=charger_config_path,
        topology_configured=True,
        primary_rpc_configured=False,
    )

__all__ = [name for name in globals() if not name.startswith("__")]
