# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import configparser
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
import unittest

from venus_evcharger.backend.config import load_runtime_backend_summary
from venus_evcharger.backend.factory import (
    _adapter_type_from_config_path,
    _normalized_path,
    _resolved_meter_backend,
    _resolved_switch_backend,
    _topology_backend_roles,
    build_service_backends,
)
from venus_evcharger.backend.goe_charger import GoEChargerBackend
from venus_evcharger.backend.models import BackendRuntimeSummary
from venus_evcharger.backend.registry import create_meter_backend
from venus_evcharger.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from venus_evcharger.backend.shelly_meter import ShellyMeterBackend
from venus_evcharger.backend.shelly_switch import ShellySwitchBackend
from venus_evcharger.backend.switch_group import SwitchGroupBackend
from venus_evcharger.backend.template_meter import TemplateMeterBackend
from venus_evcharger.backend.template_switch import TemplateSwitchBackend
from venus_evcharger.topology.config import parse_topology_config


def _service_from_backends_config(
    *,
    mode: str = "combined",
    meter_type: str = "shelly_combined",
    switch_type: str = "shelly_combined",
    charger_type: str | None = None,
    meter_config_path: str = "",
    switch_config_path: str = "",
    charger_config_path: str = "",
    host: str = "192.0.2.20",
    phase: str = "L1",
) -> SimpleNamespace:
    parser = configparser.ConfigParser()
    parser.read_string(
        f"""
[DEFAULT]
Host={host}

[Backends]
Mode={mode}
MeterType={meter_type}
SwitchType={switch_type}
ChargerType={charger_type or ""}
MeterConfigPath={meter_config_path}
SwitchConfigPath={switch_config_path}
ChargerConfigPath={charger_config_path}
"""
    )
    return SimpleNamespace(
        config=parser,
        phase=phase,
        host=host,
        pm_component="Switch",
        pm_id=0,
        max_current=16.0,
        session=MagicMock(),
    )


__all__ = [name for name in globals() if not name.startswith("__")]
