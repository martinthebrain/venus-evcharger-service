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
    compat_legacy_backend_view_from_config,
    compat_legacy_backend_view_from_runtime,
    load_runtime_backend_summary,
    runtime_summary_from_service,
    runtime_summary_is_configured,
    runtime_summary_uses_legacy_primary_rpc,
)
from venus_evcharger.backend.config_summary import _build_runtime_summary
from venus_evcharger.backend.config_topology import (
    _adapter_type_from_config_path,
    _adapter_type_from_parser,
    _native_meter_type_for_actuator,
    _runtime_summary_from_topology,
    _topology_backend_label,
)
from venus_evcharger.backend.config_normalization import (
    _configured_text,
    _legacy_meter_view_role_from_runtime,
    _legacy_switch_view_role_from_runtime,
    _normalized_text_or_default,
    _runtime_meter_role_from_legacy,
    _runtime_switch_role_from_legacy,
    normalize_backend_type,
)
from venus_evcharger.backend.models import BackendRuntimeSummary
from venus_evcharger.topology.config import (
    _actuator_type,
    _as_bool,
    _charger_type,
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
    TopologyConfigError,
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


__all__ = [name for name in globals() if not name.startswith("__")]
