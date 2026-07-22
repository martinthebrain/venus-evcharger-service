#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration builders for the DBus adapter process."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass

from venus_evcharger.core.shared import config_get_float
from venus_evcharger.dbus_adapter.jsonl import DEFAULT_COMMAND_LIFECYCLE_MAX_BYTES, DEFAULT_HEALTH_HISTORY_MAX_BYTES
from venus_evcharger.dbus_adapter.read.spec import ReadSpec, ReadSpecs
from venus_evcharger.dbus_gateway import GatewayPaths, gateway_paths
from venus_evcharger.runtime.output_path import validated_output_file_path


class CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps DBus path and option casing intact."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


@dataclass(frozen=True)
class GatewayRateSettings:
    read_interval_seconds: float
    write_interval_seconds: float
    introspection_interval_seconds: float


@dataclass(frozen=True)
class GatewayTimingSettings:
    min_tick_seconds: float
    max_tick_seconds: float
    service_list_interval_seconds: float
    cache_publish_interval_seconds: float


@dataclass(frozen=True)
class GatewaySloSettings:
    gui_max_age_seconds: float
    core_read_max_age_seconds: float
    queue_max_age_seconds: float
    mainloop_gap_max_ms: float


@dataclass(frozen=True)
class GatewayFileSettings:
    command_lifecycle_path: str
    command_lifecycle_max_bytes: int
    health_log_path: str
    health_log_interval_seconds: float
    health_log_max_bytes: int


@dataclass(frozen=True)
class GatewayIntrospectionSettings:
    snapshot_path: str
    enabled: bool


@dataclass(frozen=True)
class GatewayAdapterSettings:
    paths: GatewayPaths
    service_name: str
    device_instance: int
    read_specs: ReadSpecs
    rates: GatewayRateSettings
    timing: GatewayTimingSettings
    slo: GatewaySloSettings
    files: GatewayFileSettings
    introspection: GatewayIntrospectionSettings
    stale_after_seconds: float


def load_adapter_config(path: str) -> configparser.ConfigParser:
    parser = CasePreservingConfigParser()
    loaded = parser.read(path)
    if not loaded:
        raise ValueError(f"Unable to read config file: {path}")
    return parser


def adapter_settings(
    defaults: configparser.SectionProxy,
    *,
    explicit_paths: GatewayPaths | None = None,
) -> GatewayAdapterSettings:
    paths = explicit_paths or gateway_paths(defaults.get("DbusGatewayRunDir"))
    device_instance = configured_device_instance(defaults)
    return GatewayAdapterSettings(
        paths=paths,
        service_name=evcharger_service_name(defaults),
        device_instance=device_instance,
        read_specs=configured_read_specs(defaults),
        rates=rate_settings(defaults),
        timing=timing_settings(defaults),
        slo=slo_settings(defaults),
        files=file_settings(defaults, paths),
        introspection=introspection_settings(defaults, device_instance),
        stale_after_seconds=config_get_float(defaults, "DbusGatewayStaleAfterSeconds", 10.0),
    )


def evcharger_service_name(defaults: configparser.SectionProxy) -> str:
    base = str(defaults.get("ServiceName", "com.victronenergy.evcharger")).strip() or "com.victronenergy.evcharger"
    return f"{base}.http_{configured_device_instance(defaults)}"


def configured_device_instance(defaults: configparser.SectionProxy) -> int:
    configured = defaults.get("DeviceInstance")
    if configured is None:
        return 60
    value = str(configured).strip()
    if not value:
        return 60
    try:
        return int(value)
    except ValueError:
        return 60


def configured_read_specs(defaults: configparser.SectionProxy) -> ReadSpecs:
    battery_service = _battery_service(defaults)
    return {
        "grid_power_w": _grid_read_spec(defaults),
        "pv_power_w": _pv_read_spec(defaults),
        "battery_soc": _battery_read_spec(defaults, battery_service),
    }


def rate_settings(defaults: configparser.SectionProxy) -> GatewayRateSettings:
    return GatewayRateSettings(
        read_interval_seconds=config_get_float(defaults, "DbusGatewayReadIntervalSeconds", 0.25),
        write_interval_seconds=config_get_float(defaults, "DbusGatewayWriteIntervalSeconds", 0.35),
        introspection_interval_seconds=config_get_float(defaults, "DbusGatewayIntrospectionIntervalSeconds", 2.0),
    )


def timing_settings(defaults: configparser.SectionProxy) -> GatewayTimingSettings:
    configured_tick = config_get_float(defaults, "DbusGatewayTickSeconds", 0.2)
    min_tick = max(0.05, config_get_float(defaults, "DbusGatewayMinTickSeconds", configured_tick))
    return GatewayTimingSettings(
        min_tick_seconds=min_tick,
        max_tick_seconds=max(min_tick, config_get_float(defaults, "DbusGatewayMaxTickSeconds", 1.0)),
        service_list_interval_seconds=config_get_float(defaults, "DbusGatewayServiceListIntervalSeconds", 900.0),
        cache_publish_interval_seconds=max(
            0.0,
            config_get_float(defaults, "DbusGatewayCachePublishIntervalSeconds", 0.0),
        ),
    )


def slo_settings(defaults: configparser.SectionProxy) -> GatewaySloSettings:
    return GatewaySloSettings(
        gui_max_age_seconds=max(0.1, config_get_float(defaults, "DbusGatewaySloGuiMaxAgeSeconds", 2.0)),
        core_read_max_age_seconds=max(0.1, config_get_float(defaults, "DbusGatewaySloCoreReadMaxAgeSeconds", 5.0)),
        queue_max_age_seconds=max(0.1, config_get_float(defaults, "DbusGatewaySloQueueMaxAgeSeconds", 10.0)),
        mainloop_gap_max_ms=max(10.0, config_get_float(defaults, "DbusGatewaySloMainloopGapMaxMs", 500.0)),
    )


def file_settings(defaults: configparser.SectionProxy, paths: GatewayPaths) -> GatewayFileSettings:
    return GatewayFileSettings(
        command_lifecycle_path=validated_output_file_path(
            defaults.get(
                "DbusGatewayCommandLifecyclePath",
                os.path.join(paths.run_dir, "dbus-command-lifecycle.jsonl"),
            ),
            label="DbusGatewayCommandLifecyclePath",
            suffix=".jsonl",
        ),
        command_lifecycle_max_bytes=max(
            0,
            int(config_get_float(defaults, "DbusGatewayCommandLifecycleMaxBytes", DEFAULT_COMMAND_LIFECYCLE_MAX_BYTES)),
        ),
        health_log_path=validated_output_file_path(
            defaults.get("DbusGatewayHealthLogPath", os.path.join(paths.run_dir, "dbus-health-history.jsonl")),
            label="DbusGatewayHealthLogPath",
            suffix=".jsonl",
        ),
        health_log_interval_seconds=max(
            0.0,
            config_get_float(defaults, "DbusGatewayHealthLogIntervalSeconds", 10.0),
        ),
        health_log_max_bytes=max(
            0,
            int(config_get_float(defaults, "DbusGatewayHealthLogMaxBytes", DEFAULT_HEALTH_HISTORY_MAX_BYTES)),
        ),
    )


def introspection_settings(
    defaults: configparser.SectionProxy,
    device_instance: int,
) -> GatewayIntrospectionSettings:
    return GatewayIntrospectionSettings(
        snapshot_path=validated_output_file_path(
            defaults.get(
                "DbusIntrospectionSnapshotPath",
                f"/run/dbus-venus-evcharger-dbus-map-{device_instance}.json",
            ),
            label="DbusIntrospectionSnapshotPath",
            suffix=".json",
        ),
        enabled=_truthy(defaults.get("DbusIntrospectionEnabled", "1")),
    )


def _grid_read_spec(defaults: configparser.SectionProxy) -> ReadSpec:
    grid_paths = [
        str(defaults.get("AutoGridL1Path", "/Ac/Grid/L1/Power")).strip(),
        str(defaults.get("AutoGridL2Path", "/Ac/Grid/L2/Power")).strip(),
        str(defaults.get("AutoGridL3Path", "/Ac/Grid/L3/Power")).strip(),
    ]
    return {
        "service": str(defaults.get("AutoGridService", "com.victronenergy.system")).strip(),
        "paths": [path for path in grid_paths if path],
        "interval": 2.0,
        "aggregate": "sum",
        "priority": "read",
    }


def _pv_read_spec(defaults: configparser.SectionProxy) -> ReadSpec:
    return {
        "service": str(defaults.get("AutoPvService", "")).strip(),
        "prefix": str(defaults.get("AutoPvServicePrefix", "com.victronenergy.pvinverter")).strip(),
        "path": str(defaults.get("AutoPvPath", "/Ac/Power")).strip(),
        "dc_service": str(defaults.get("AutoDcPvService", "com.victronenergy.system")).strip(),
        "dc_path": str(defaults.get("AutoDcPvPath", "/Dc/Pv/Power")).strip(),
        "use_dc_pv": _truthy(defaults.get("AutoUseDcPv", "1")),
        "interval": 2.0,
        "aggregate": "pv-total",
        "priority": "read",
        "optional_zero_on_error": True,
        "optional_confidence": 0.2,
    }


def _battery_read_spec(defaults: configparser.SectionProxy, battery_service: str) -> ReadSpec:
    return {
        "service": battery_service,
        "prefix": str(defaults.get("AutoBatteryServicePrefix", "com.victronenergy.battery")).strip(),
        "path": str(defaults.get("AutoBatterySocPath", "/Dc/Battery/Soc")).strip(),
        "aggregate": "first-service" if not battery_service else "",
        "interval": 2.0,
        "priority": "read",
    }


def _battery_service(defaults: configparser.SectionProxy) -> str:
    service = str(defaults.get("AutoBatteryService", "")).strip()
    return "" if service.endswith(".example") else service


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")
