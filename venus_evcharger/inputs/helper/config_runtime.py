# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed configuration for the auto-input helper process."""

from __future__ import annotations

import configparser
import os
import uuid
from dataclasses import dataclass

from venus_evcharger.core.shared import config_get_float, parse_config_bool
from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig


@dataclass(frozen=True, slots=True)
class AutoInputHelperSettings:
    """Complete immutable configuration consumed by helper components."""

    config_path: str
    snapshot_path: str
    parent_pid: int | None
    helper_generation: int
    runtime_instance_id: str
    gateway_run_dir: str
    gateway_cache_path: str
    gateway_max_age_seconds: float
    gateway_error_retry_seconds: float
    auto_pv_poll_interval_seconds: float
    auto_grid_poll_interval_seconds: float
    auto_battery_poll_interval_seconds: float
    poll_interval_seconds: float
    grid_fusion_config: GridFusionConfig
    validation_poll_seconds: float
    topology_refresh_seconds: float


def load_auto_input_helper_settings(
    config_path: str,
    snapshot_path: str | None,
    parent_pid: object,
    helper_generation: object,
    runtime_instance_id: object,
) -> AutoInputHelperSettings:
    """Load and validate one complete helper configuration."""
    parser = configparser.ConfigParser()
    loaded = parser.read(config_path)
    _require_loaded_config(parser, loaded, config_path)
    config = parser["DEFAULT"]
    poll_fallback_ms = config_get_float(config, "PollIntervalMs", 1000.0)
    auto_poll_ms = config_get_float(config, "AutoInputPollIntervalMs", poll_fallback_ms)
    pv_poll = _poll_interval_seconds(config, "AutoPvPollIntervalMs", auto_poll_ms)
    grid_poll = _poll_interval_seconds(config, "AutoGridPollIntervalMs", auto_poll_ms)
    battery_poll = _poll_interval_seconds(config, "AutoBatteryPollIntervalMs", auto_poll_ms)
    gateway_max_age = max(0.0, config_get_float(config, "DbusGatewayMaxAgeSeconds", 10.0))
    fusion = _grid_fusion_config(config, gateway_max_age)
    _validate_grid_fusion_poll_interval(fusion, battery_poll)
    return AutoInputHelperSettings(
        config_path=config_path,
        snapshot_path=snapshot_path or config.get("AutoInputSnapshotPath", "/run/dbus-venus-evcharger-auto.json").strip(),
        parent_pid=_parsed_parent_pid(parent_pid),
        helper_generation=_parsed_helper_generation(helper_generation),
        runtime_instance_id=_parsed_runtime_instance_id(runtime_instance_id),
        gateway_run_dir=config.get("DbusGatewayRunDir", "/run/venus-evcharger").strip(),
        gateway_cache_path=_gateway_cache_path(config),
        gateway_max_age_seconds=gateway_max_age,
        gateway_error_retry_seconds=max(
            1.0, min(300.0, config_get_float(config, "DbusGatewayErrorRetrySeconds", 30.0))
        ),
        auto_pv_poll_interval_seconds=pv_poll,
        auto_grid_poll_interval_seconds=grid_poll,
        auto_battery_poll_interval_seconds=battery_poll,
        poll_interval_seconds=min(max(0.2, auto_poll_ms / 1000.0), pv_poll, grid_poll, battery_poll),
        grid_fusion_config=fusion,
        validation_poll_seconds=max(5.0, config_get_float(config, "AutoInputValidationPollSeconds", 30.0)),
        topology_refresh_seconds=max(5.0, config_get_float(config, "EnergyTopologyRefreshSeconds", 60.0)),
    )


def _gateway_cache_path(config: configparser.SectionProxy) -> str:
    run_dir = config.get("DbusGatewayRunDir", "/run/venus-evcharger").strip()
    return config.get("DbusGatewayCachePath", os.path.join(run_dir, "dbus-cache.json")).strip()


def _require_loaded_config(parser: configparser.ConfigParser, loaded: list[str], config_path: str) -> None:
    if not loaded or "DEFAULT" not in parser:
        raise ValueError(f"Unable to read config file: {config_path}")


def _validate_grid_fusion_poll_interval(fusion: GridFusionConfig, battery_poll: float) -> None:
    if fusion.enabled and fusion.primary_max_age_seconds < battery_poll:
        raise ValueError("AutoGridFusionPrimaryMaxAgeSeconds must cover AutoBatteryPollIntervalMs")


def _poll_interval_seconds(config: configparser.SectionProxy, key: str, fallback_ms: float) -> float:
    return max(0.2, config_get_float(config, key, fallback_ms) / 1000.0)


def _grid_fusion_config(config: configparser.SectionProxy, gateway_max_age_seconds: float) -> GridFusionConfig:
    enabled = parse_config_bool(config["AutoGridFusionEnabled"]) if "AutoGridFusionEnabled" in config else False
    backup_max_age_seconds = gateway_max_age_seconds
    if enabled:
        backup_max_age_seconds = config_get_float(config, "AutoGridFusionBackupMaxAgeSeconds", 6.0)
    return GridFusionConfig(
        enabled=enabled,
        primary_source_id=config.get("AutoGridFusionPrimarySource", "").strip(),
        backup_source_id=config.get("AutoGridFusionBackupSource", "victron").strip(),
        primary_max_age_seconds=config_get_float(config, "AutoGridFusionPrimaryMaxAgeSeconds", 15.0),
        backup_max_age_seconds=backup_max_age_seconds,
        minimum_confidence=config_get_float(config, "AutoGridFusionMinimumConfidence", 0.5),
        failover_samples=int(config_get_float(config, "AutoGridFusionFailoverSamples", 3.0)),
        recovery_samples=int(config_get_float(config, "AutoGridFusionRecoverySamples", 15.0)),
        failover_hold_seconds=config_get_float(config, "AutoGridFusionFailoverHoldSeconds", 6.0),
        mismatch_absolute_watts=config_get_float(config, "AutoGridFusionMismatchAbsoluteWatts", 300.0),
        mismatch_relative=config_get_float(config, "AutoGridFusionMismatchRelative", 0.15),
        mismatch_samples=int(config_get_float(config, "AutoGridFusionMismatchSamples", 3.0)),
        future_tolerance_seconds=config_get_float(config, "AutoGridFusionFutureToleranceSeconds", 1.0),
    )


def _parsed_parent_pid(value: object) -> int | None:
    return int(value) if isinstance(value, (str, int)) else None


def _parsed_helper_generation(value: object) -> int:
    return max(0, int(value)) if isinstance(value, (str, int)) else 0


def _parsed_runtime_instance_id(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return uuid.uuid4().hex
