# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed configuration for the auto-input helper process."""

from __future__ import annotations

import configparser
import uuid
from dataclasses import dataclass
from typing import TypeGuard

from venus_evcharger.core.shared import config_get_float, parse_config_bool
from venus_evcharger.energy.config import load_energy_source_settings
from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig
from venus_evcharger.energy.models import ENERGY_SOURCE_CONNECTOR_TYPES, EnergySourceDefinition
from venus_evcharger.inputs.helper.external_contracts import (
    MAX_EXTERNAL_CYCLE_BUDGET_SECONDS,
    PV_SOURCE_POLICIES,
    ExternalPollingPolicy,
    PvProjectionPolicy,
    PvSourcePolicyName,
)
from venus_evcharger.ipc.gateway_path_config import (
    GatewayPaths,
    configured_gateway_paths,
)

_SEMANTIC_GATEWAY_ALIASES = frozenset({"primary_battery", "victron"})
_DEFAULT_PV_POLICY = PvProjectionPolicy()


class _ConfigKey:
    """Canonical names at the case-insensitive ConfigParser boundary."""

    SNAPSHOT_PATH = "AutoInputSnapshotPath"
    GATEWAY_MAX_AGE = "DbusGatewayMaxAgeSeconds"
    GATEWAY_ERROR_RETRY = "DbusGatewayErrorRetrySeconds"
    POLL_INTERVAL_MS = "PollIntervalMs"
    AUTO_POLL_INTERVAL_MS = "AutoInputPollIntervalMs"
    PV_POLL_INTERVAL_MS = "AutoPvPollIntervalMs"
    GRID_POLL_INTERVAL_MS = "AutoGridPollIntervalMs"
    BATTERY_POLL_INTERVAL_MS = "AutoBatteryPollIntervalMs"
    VALIDATION_POLL = "AutoInputValidationPollSeconds"
    TOPOLOGY_REFRESH = "EnergyTopologyRefreshSeconds"
    GRID_FUSION_ENABLED = "AutoGridFusionEnabled"
    GRID_PRIMARY_SOURCE = "AutoGridFusionPrimarySource"
    GRID_BACKUP_SOURCE = "AutoGridFusionBackupSource"
    GRID_PRIMARY_MAX_AGE = "AutoGridFusionPrimaryMaxAgeSeconds"
    GRID_BACKUP_MAX_AGE = "AutoGridFusionBackupMaxAgeSeconds"
    GRID_MINIMUM_CONFIDENCE = "AutoGridFusionMinimumConfidence"
    GRID_FAILOVER_SAMPLES = "AutoGridFusionFailoverSamples"
    GRID_RECOVERY_SAMPLES = "AutoGridFusionRecoverySamples"
    GRID_FAILOVER_HOLD = "AutoGridFusionFailoverHoldSeconds"
    GRID_MISMATCH_WATTS = "AutoGridFusionMismatchAbsoluteWatts"
    GRID_MISMATCH_RELATIVE = "AutoGridFusionMismatchRelative"
    GRID_MISMATCH_SAMPLES = "AutoGridFusionMismatchSamples"
    GRID_FUTURE_TOLERANCE = "AutoGridFusionFutureToleranceSeconds"
    EXTERNAL_REQUEST_TIMEOUT = "ExternalEnergySourceRequestTimeoutSeconds"
    EXTERNAL_POLL_INTERVAL = "ExternalEnergySourcePollIntervalSeconds"
    EXTERNAL_BACKOFF_BASE = "ExternalEnergySourceBackoffBaseSeconds"
    EXTERNAL_BACKOFF_MAX = "ExternalEnergySourceBackoffMaxSeconds"
    EXTERNAL_LAST_GOOD_MAX_AGE = "ExternalEnergySourceLastGoodMaxAgeSeconds"
    EXTERNAL_CYCLE_BUDGET = "ExternalEnergySourceCycleBudgetSeconds"
    PV_SOURCE_POLICY = "AutoPvSourcePolicy"
    PV_EXTERNAL_SOURCE = "AutoPvExternalSource"


@dataclass(frozen=True, slots=True)
class AutoInputHelperSettings:
    """Complete immutable configuration consumed by helper components."""

    config_path: str
    snapshot_path: str
    parent_pid: int | None
    helper_generation: int
    runtime_instance_id: str
    gateway_paths: GatewayPaths
    gateway_max_age_seconds: float
    gateway_error_retry_seconds: float
    auto_pv_poll_interval_seconds: float
    auto_grid_poll_interval_seconds: float
    auto_battery_poll_interval_seconds: float
    poll_interval_seconds: float
    grid_fusion_config: GridFusionConfig
    gateway_energy_source: EnergySourceDefinition | None
    energy_sources: tuple[EnergySourceDefinition, ...]
    use_combined_battery_soc: bool
    energy_source_request_timeout_seconds: float
    external_polling_policy: ExternalPollingPolicy
    pv_projection_policy: PvProjectionPolicy
    validation_poll_seconds: float
    topology_refresh_seconds: float


@dataclass(frozen=True, slots=True)
class _RuntimeIdentity:
    parent_pid: int | None
    helper_generation: int
    runtime_instance_id: str


@dataclass(frozen=True, slots=True)
class _GatewaySettings:
    paths: GatewayPaths
    max_age_seconds: float
    error_retry_seconds: float


@dataclass(frozen=True, slots=True)
class _PollingSettings:
    pv_seconds: float
    grid_seconds: float
    battery_seconds: float
    fastest_source_seconds: float
    loop_seconds: float
    validation_seconds: float
    topology_seconds: float


@dataclass(frozen=True, slots=True)
class _EnergySettings:
    gateway_source: EnergySourceDefinition | None
    external_sources: tuple[EnergySourceDefinition, ...]
    use_combined_battery_soc: bool
    request_timeout_seconds: float
    polling_policy: ExternalPollingPolicy
    pv_policy: PvProjectionPolicy


def load_auto_input_helper_settings(
    config_path: str,
    snapshot_path: str | None,
    parent_pid: object,
    helper_generation: object,
    runtime_instance_id: object,
) -> AutoInputHelperSettings:
    """Load and validate one complete helper configuration."""
    config = _read_config(config_path)
    identity = _runtime_identity(parent_pid, helper_generation, runtime_instance_id)
    gateway = _gateway_settings(config)
    polling = _polling_settings(config)
    fusion = _grid_fusion_config(config, gateway.max_age_seconds)
    energy = _energy_settings(config, fusion, polling)
    _validate_grid_fusion_poll_interval(fusion, polling.battery_seconds)
    return AutoInputHelperSettings(
        config_path=config_path,
        snapshot_path=snapshot_path or _text(config, _ConfigKey.SNAPSHOT_PATH, "/run/dbus-venus-evcharger-auto.json"),
        parent_pid=identity.parent_pid,
        helper_generation=identity.helper_generation,
        runtime_instance_id=identity.runtime_instance_id,
        gateway_paths=gateway.paths,
        gateway_max_age_seconds=gateway.max_age_seconds,
        gateway_error_retry_seconds=gateway.error_retry_seconds,
        auto_pv_poll_interval_seconds=polling.pv_seconds,
        auto_grid_poll_interval_seconds=polling.grid_seconds,
        auto_battery_poll_interval_seconds=polling.battery_seconds,
        poll_interval_seconds=polling.loop_seconds,
        grid_fusion_config=fusion,
        gateway_energy_source=energy.gateway_source,
        energy_sources=energy.external_sources,
        use_combined_battery_soc=energy.use_combined_battery_soc,
        energy_source_request_timeout_seconds=energy.request_timeout_seconds,
        external_polling_policy=energy.polling_policy,
        pv_projection_policy=energy.pv_policy,
        validation_poll_seconds=polling.validation_seconds,
        topology_refresh_seconds=polling.topology_seconds,
    )


def _read_config(config_path: str) -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    if not parser.read(config_path):
        raise ValueError(f"Unable to read config file: {config_path}")
    return parser["DEFAULT"]


def _text(config: configparser.SectionProxy, key: str, default: str) -> str:
    return config.get(key, default).strip()


def _bounded_float(
    config: configparser.SectionProxy,
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    value = float(max(minimum, config_get_float(config, key, default)))
    return value if maximum is None else min(maximum, value)


def _runtime_identity(
    parent_pid: object,
    helper_generation: object,
    runtime_instance_id: object,
) -> _RuntimeIdentity:
    return _RuntimeIdentity(
        parent_pid=int(parent_pid) if isinstance(parent_pid, (str, int)) else None,
        helper_generation=max(0, int(helper_generation)) if isinstance(helper_generation, (str, int)) else 0,
        runtime_instance_id=_runtime_instance_id(runtime_instance_id),
    )


def _runtime_instance_id(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return uuid.uuid4().hex


def _gateway_settings(config: configparser.SectionProxy) -> _GatewaySettings:
    return _GatewaySettings(
        paths=configured_gateway_paths(config),
        max_age_seconds=_bounded_float(
            config,
            _ConfigKey.GATEWAY_MAX_AGE,
            10.0,
            minimum=0.0,
        ),
        error_retry_seconds=_bounded_float(
            config,
            _ConfigKey.GATEWAY_ERROR_RETRY,
            30.0,
            minimum=1.0,
            maximum=300.0,
        ),
    )


def _polling_settings(config: configparser.SectionProxy) -> _PollingSettings:
    poll_fallback_ms = config_get_float(config, _ConfigKey.POLL_INTERVAL_MS, 1000.0)
    auto_poll_ms = config_get_float(config, _ConfigKey.AUTO_POLL_INTERVAL_MS, poll_fallback_ms)
    pv_seconds = _poll_interval_seconds(config, _ConfigKey.PV_POLL_INTERVAL_MS, auto_poll_ms)
    grid_seconds = _poll_interval_seconds(config, _ConfigKey.GRID_POLL_INTERVAL_MS, auto_poll_ms)
    battery_seconds = _poll_interval_seconds(config, _ConfigKey.BATTERY_POLL_INTERVAL_MS, auto_poll_ms)
    fastest_source_seconds = min(pv_seconds, grid_seconds, battery_seconds)
    auto_poll_seconds = max(0.2, auto_poll_ms / 1000.0)
    return _PollingSettings(
        pv_seconds=pv_seconds,
        grid_seconds=grid_seconds,
        battery_seconds=battery_seconds,
        fastest_source_seconds=fastest_source_seconds,
        loop_seconds=min(auto_poll_seconds, fastest_source_seconds),
        validation_seconds=_bounded_float(
            config,
            _ConfigKey.VALIDATION_POLL,
            30.0,
            minimum=5.0,
        ),
        topology_seconds=_bounded_float(
            config,
            _ConfigKey.TOPOLOGY_REFRESH,
            60.0,
            minimum=5.0,
        ),
    )


def _poll_interval_seconds(config: configparser.SectionProxy, key: str, fallback_ms: float) -> float:
    return _bounded_float(config, key, fallback_ms, minimum=200.0) / 1000.0


def _grid_fusion_config(
    config: configparser.SectionProxy,
    gateway_max_age_seconds: float,
) -> GridFusionConfig:
    enabled = parse_config_bool(config.get(_ConfigKey.GRID_FUSION_ENABLED))
    backup_max_age_seconds = gateway_max_age_seconds
    if enabled:
        backup_max_age_seconds = config_get_float(config, _ConfigKey.GRID_BACKUP_MAX_AGE, 6.0)
    return GridFusionConfig(
        enabled=enabled,
        primary_source_id=_text(config, _ConfigKey.GRID_PRIMARY_SOURCE, ""),
        backup_source_id=_text(config, _ConfigKey.GRID_BACKUP_SOURCE, "victron"),
        primary_max_age_seconds=config_get_float(config, _ConfigKey.GRID_PRIMARY_MAX_AGE, 15.0),
        backup_max_age_seconds=backup_max_age_seconds,
        minimum_confidence=config_get_float(config, _ConfigKey.GRID_MINIMUM_CONFIDENCE, 0.5),
        failover_samples=int(config_get_float(config, _ConfigKey.GRID_FAILOVER_SAMPLES, 3.0)),
        recovery_samples=int(config_get_float(config, _ConfigKey.GRID_RECOVERY_SAMPLES, 15.0)),
        failover_hold_seconds=config_get_float(config, _ConfigKey.GRID_FAILOVER_HOLD, 6.0),
        mismatch_absolute_watts=config_get_float(config, _ConfigKey.GRID_MISMATCH_WATTS, 300.0),
        mismatch_relative=config_get_float(config, _ConfigKey.GRID_MISMATCH_RELATIVE, 0.15),
        mismatch_samples=int(config_get_float(config, _ConfigKey.GRID_MISMATCH_SAMPLES, 3.0)),
        future_tolerance_seconds=config_get_float(config, _ConfigKey.GRID_FUTURE_TOLERANCE, 1.0),
    )


def _validate_grid_fusion_poll_interval(fusion: GridFusionConfig, battery_poll: float) -> None:
    if fusion.enabled and fusion.primary_max_age_seconds < battery_poll:
        raise ValueError("AutoGridFusionPrimaryMaxAgeSeconds must cover AutoBatteryPollIntervalMs")


def _energy_settings(
    config: configparser.SectionProxy,
    fusion: GridFusionConfig,
    polling: _PollingSettings,
) -> _EnergySettings:
    configured_sources, use_combined_soc = load_energy_source_settings(config)
    gateway_source, external_sources = _split_energy_sources(configured_sources)
    pv_policy = _pv_projection_policy(config)
    external_ids = tuple(source.source_id for source in external_sources)
    _validate_external_source_references(external_ids, fusion, pv_policy)
    return _EnergySettings(
        gateway_source=gateway_source,
        external_sources=external_sources,
        use_combined_battery_soc=use_combined_soc,
        request_timeout_seconds=_bounded_float(
            config,
            _ConfigKey.EXTERNAL_REQUEST_TIMEOUT,
            2.0,
            minimum=0.1,
        ),
        polling_policy=_external_polling_policy(
            config,
            polling.fastest_source_seconds,
        ),
        pv_policy=pv_policy,
    )


def _split_energy_sources(
    sources: tuple[EnergySourceDefinition, ...],
) -> tuple[EnergySourceDefinition | None, tuple[EnergySourceDefinition, ...]]:
    source_ids = tuple(source.source_id for source in sources)
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("AutoEnergySources contains duplicate source ids")
    gateway_source: EnergySourceDefinition | None = None
    external_sources: list[EnergySourceDefinition] = []
    for source in sources:
        if source.source_id in _SEMANTIC_GATEWAY_ALIASES:
            gateway_source = source
            continue
        _validate_external_connector(source)
        external_sources.append(source)
    return gateway_source, tuple(external_sources)


def _validate_external_connector(source: EnergySourceDefinition) -> None:
    if source.connector_type in ENERGY_SOURCE_CONNECTOR_TYPES:
        return
    profile = source.profile_name or "<none>"
    raise ValueError(
        f"Auto energy source '{source.source_id}' profile '{profile}' has no supported non-DBus connector; "
        "Victron/DBus values must come from the semantic DBus gateway snapshot"
    )


def _validate_external_source_references(
    source_ids: tuple[str, ...],
    fusion: GridFusionConfig,
    policy: PvProjectionPolicy,
) -> None:
    _validate_grid_primary_source(source_ids, fusion)
    _validate_explicit_pv_source(source_ids, policy)
    _validate_pv_policy_has_external_source(source_ids, policy)


def _validate_grid_primary_source(
    source_ids: tuple[str, ...],
    fusion: GridFusionConfig,
) -> None:
    if fusion.enabled and fusion.primary_source_id not in source_ids:
        raise ValueError(
            f"AutoGridFusionPrimarySource '{fusion.primary_source_id}' is not present in external AutoEnergySources"
        )


def _validate_explicit_pv_source(
    source_ids: tuple[str, ...],
    policy: PvProjectionPolicy,
) -> None:
    if policy.external_source_id and policy.external_source_id not in source_ids:
        raise ValueError(
            f"AutoPvExternalSource '{policy.external_source_id}' is not present in external AutoEnergySources"
        )


def _validate_pv_policy_has_external_source(
    source_ids: tuple[str, ...],
    policy: PvProjectionPolicy,
) -> None:
    if policy.name in {"external_preferred", "external_only"} and not source_ids:
        raise ValueError(f"AutoPvSourcePolicy '{policy.name}' requires an external energy source")


def _external_polling_policy(
    config: configparser.SectionProxy,
    poll_fallback_seconds: float,
) -> ExternalPollingPolicy:
    return ExternalPollingPolicy(
        poll_interval_seconds=_bounded_float(
            config,
            _ConfigKey.EXTERNAL_POLL_INTERVAL,
            poll_fallback_seconds,
            minimum=0.2,
        ),
        backoff_base_seconds=_bounded_float(
            config,
            _ConfigKey.EXTERNAL_BACKOFF_BASE,
            5.0,
            minimum=0.1,
        ),
        backoff_max_seconds=_bounded_float(
            config,
            _ConfigKey.EXTERNAL_BACKOFF_MAX,
            60.0,
            minimum=0.1,
        ),
        last_good_max_age_seconds=_bounded_float(
            config,
            _ConfigKey.EXTERNAL_LAST_GOOD_MAX_AGE,
            30.0,
            minimum=0.0,
        ),
        cycle_budget_seconds=_bounded_float(
            config,
            _ConfigKey.EXTERNAL_CYCLE_BUDGET,
            2.0,
            minimum=0.05,
            maximum=MAX_EXTERNAL_CYCLE_BUDGET_SECONDS,
        ),
    )


def _pv_projection_policy(config: configparser.SectionProxy) -> PvProjectionPolicy:
    raw_name = _text(config, _ConfigKey.PV_SOURCE_POLICY, _DEFAULT_PV_POLICY.name).lower().replace("-", "_")
    if not _is_pv_source_policy(raw_name):
        raise ValueError(f"Unsupported AutoPvSourcePolicy '{raw_name}'")
    return PvProjectionPolicy(
        name=raw_name,
        external_source_id=_text(config, _ConfigKey.PV_EXTERNAL_SOURCE, ""),
    )


def _is_pv_source_policy(value: str) -> TypeGuard[PvSourcePolicyName]:
    return value in PV_SOURCE_POLICIES
