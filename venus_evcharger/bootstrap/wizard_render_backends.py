# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy backend-line rendering for wizard output."""

from __future__ import annotations

import configparser

from venus_evcharger.topology import EvChargerTopologyConfig, MeasurementConfig


class _CasePreservingConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def render_legacy_backends_from_topology(
    topology_config: EvChargerTopologyConfig,
    adapter_files: dict[str, str],
) -> list[str]:
    if topology_config.topology.type == "simple_relay" and not adapter_files:
        return []
    lines = ["Mode=split"]
    lines.extend(_measurement_backend_lines(topology_config.measurement, adapter_files))
    lines.extend(_actuator_backend_lines(topology_config))
    lines.extend(_charger_backend_lines(topology_config))
    return lines


def _measurement_backend_lines(
    measurement: MeasurementConfig | None,
    adapter_files: dict[str, str],
) -> list[str]:
    if measurement is None or measurement.type in {"none", "charger_native", "actuator_native", "fixed_reference", "learned_reference"}:
        return ["MeterType=none"]
    if measurement.type != "external_meter" or measurement.config_path is None:
        raise ValueError(f"unsupported legacy meter mapping for measurement type '{measurement.type}'")
    return [
        f"MeterType={_adapter_type_from_file(adapter_files, measurement.config_path)}",
        f"MeterConfigPath={measurement.config_path}",
    ]


def _actuator_backend_lines(topology_config: EvChargerTopologyConfig) -> list[str]:
    actuator = topology_config.actuator
    if actuator is None:
        return ["SwitchType=none"]
    lines = [f"SwitchType={actuator.type}"]
    if actuator.config_path:
        lines.append(f"SwitchConfigPath={actuator.config_path}")
    return lines


def _charger_backend_lines(topology_config: EvChargerTopologyConfig) -> list[str]:
    charger = topology_config.charger
    if charger is None:
        return ["ChargerType="]
    lines = [f"ChargerType={charger.type}"]
    if charger.config_path:
        lines.append(f"ChargerConfigPath={charger.config_path}")
    return lines


def _adapter_type_from_file(adapter_files: dict[str, str], relative_path: str) -> str:
    content = adapter_files.get(relative_path)
    if content is None:
        raise ValueError(f"missing adapter file '{relative_path}' while rendering legacy backends")
    parser = _CasePreservingConfigParser()
    parser.read_string(content)
    if not parser.has_section("Adapter"):
        raise ValueError(f"adapter file '{relative_path}' is missing required [Adapter] section")
    adapter_type = str(parser["Adapter"].get("Type", "")).strip()
    if not adapter_type:
        raise ValueError(f"adapter file '{relative_path}' is missing Adapter.Type")
    return adapter_type
