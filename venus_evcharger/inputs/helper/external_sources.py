# SPDX-License-Identifier: GPL-3.0-or-later
"""Coherent projections for configured non-DBus energy sources."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Protocol, TypeGuard

from venus_evcharger.energy import (
    EnergyClusterSnapshot,
    EnergyLearningProfile,
    EnergySourceDefinition,
    EnergySourceSnapshot,
    aggregate_energy_sources,
    derive_discharge_balance_metrics,
    derive_discharge_control_metrics,
    derive_energy_forecast,
    read_energy_source_step,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)
from venus_evcharger.energy.http_session import ConnectorHttpSession
from venus_evcharger.energy.read_steps import EnergySourceStepReader
from venus_evcharger.inputs.helper.contracts import Snapshot
from venus_evcharger.inputs.helper.external_pv_projection import (
    external_pv_projection,
)
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalEnergyCycle,
    ExternalPollingPolicy,
    ExternalSourcePoll,
    GatewayBatteryMeasurements,
    PvProjectionPolicy,
)
from venus_evcharger.inputs.helper.external_scheduler import ExternalSourceScheduler
from venus_evcharger.inputs.helper.external_soc import (
    _selected_soc,
    _selected_soc_observed_monotonic,
    _with_gateway_source,
)
from venus_evcharger.inputs.helper.gateway_battery import gateway_battery_source


class _ObservedPollPort(Protocol):
    @property
    def contributing(self) -> bool: ...

    @property
    def poll_status(self) -> str: ...


class _SourceIdentityPort(Protocol):
    @property
    def source_id(self) -> str: ...


class _SourcePayloadPollPort(Protocol):
    @property
    def snapshot(self) -> _SourceIdentityPort: ...

    def payload(self) -> dict[str, object]: ...


class ConfiguredEnergySources:
    """Own one bounded external-source scheduler and its domain projections."""

    def __init__(
        self,
        definitions: tuple[EnergySourceDefinition, ...],
        *,
        use_combined_soc: bool,
        request_timeout_seconds: float,
        polling_policy: ExternalPollingPolicy,
        pv_policy: PvProjectionPolicy,
        gateway_source_id: str = "victron",
        gateway_definition: EnergySourceDefinition | None = None,
        session: object | None = None,
        reader: EnergySourceStepReader = read_energy_source_step,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        clock = monotonic or time.monotonic
        self.definitions = definitions
        self.use_combined_soc = use_combined_soc
        self.gateway_source_id = gateway_source_id
        self.gateway_definition = gateway_definition
        self.pv_policy = pv_policy
        self._session = ConnectorHttpSession(bool(definitions), session)
        self._scheduler = ExternalSourceScheduler(
            definitions,
            polling_policy,
            request_timeout_seconds,
            reader,
            session=self._session.session,
            monotonic=clock,
        )
        self._learning_profiles: dict[str, EnergyLearningProfile] = {}

    def close(self) -> None:
        """Close the production-owned shared HTTP session."""
        self._session.close()

    @property
    def enabled(self) -> bool:
        return bool(self.definitions) or self.gateway_definition is not None

    def collect_cycle(
        self,
        gateway_measurements: GatewayBatteryMeasurements,
        now: float,
    ) -> ExternalEnergyCycle:
        """Poll once and reuse the same result for every semantic projection."""
        polls = self._scheduler.poll(now)
        contributing = _contributing_snapshots(polls)
        newly_observed = _newly_observed_snapshots(polls)
        gateway_source = gateway_battery_source(
            gateway_measurements,
            self.gateway_source_id,
            self.gateway_definition,
        )
        aggregate_sources = _with_gateway_source(contributing, gateway_source)
        cluster = aggregate_energy_sources(aggregate_sources)
        self._learning_profiles = update_energy_learning_profiles(
            self._learning_profiles,
            newly_observed,
            now,
        )
        learning_summary = summarize_energy_learning_profiles(self._learning_profiles)
        discharge_balance = derive_discharge_balance_metrics(contributing, self._learning_profiles)
        discharge_control = derive_discharge_control_metrics(
            contributing,
            {definition.source_id: definition for definition in self.definitions},
        )
        forecast = derive_energy_forecast(_forecast_cluster_payload(cluster), learning_summary)
        effective_soc, battery_observed_at = _selected_soc(
            cluster,
            contributing,
            gateway_source,
            self.use_combined_soc,
        )
        battery_observed_monotonic = _selected_soc_observed_monotonic(
            cluster,
            polls,
            gateway_source,
            gateway_measurements.soc,
            self.use_combined_soc,
        )
        battery = _battery_payload(
            effective_soc,
            cluster.as_dict(),
            forecast,
            discharge_balance,
            discharge_control,
            _source_payloads(
                polls,
                gateway_source,
                (
                    None
                    if gateway_measurements.primary is None
                    else gateway_measurements.primary.observed_monotonic
                ),
                discharge_balance,
                discharge_control,
            ),
            self._learning_profiles,
        )
        return ExternalEnergyCycle(
            battery=battery,
            pv=external_pv_projection(polls, self.pv_policy.external_source_id),
            battery_observed_at=battery_observed_at,
            polls=polls,
            battery_observed_monotonic=battery_observed_monotonic,
        )


def _contributing_snapshots(
    polls: tuple[ExternalSourcePoll, ...],
) -> tuple[EnergySourceSnapshot, ...]:
    return tuple(poll.snapshot for poll in polls if poll.contributing)


def _newly_observed_snapshots(
    polls: tuple[ExternalSourcePoll, ...],
) -> tuple[EnergySourceSnapshot, ...]:
    return tuple(poll.snapshot for poll in polls if _newly_observed(poll))


def _newly_observed(poll: _ObservedPollPort) -> bool:
    return poll.contributing and poll.poll_status == "success"




def _forecast_cluster_payload(cluster: EnergyClusterSnapshot) -> dict[str, object]:
    return {
        "battery_combined_soc": cluster.combined_soc,
        "battery_combined_charge_power_w": cluster.combined_charge_power_w,
        "battery_combined_discharge_power_w": cluster.combined_discharge_power_w,
        "battery_combined_charge_limit_power_w": cluster.combined_charge_limit_power_w,
        "battery_combined_discharge_limit_power_w": cluster.combined_discharge_limit_power_w,
        "battery_combined_grid_interaction_w": cluster.combined_grid_interaction_w,
    }


def _source_payloads(
    polls: tuple[ExternalSourcePoll, ...],
    gateway: EnergySourceSnapshot | None,
    gateway_observed_monotonic: float | None,
    discharge_balance: Mapping[str, object],
    discharge_control: Mapping[str, object],
) -> list[dict[str, object]]:
    balance_sources = _nested_mappings(discharge_balance.get("sources"))
    control_sources = _nested_mappings(discharge_control.get("sources"))
    payloads = [
        _external_source_payload(poll, balance_sources, control_sources)
        for poll in polls
    ]
    if gateway is not None:
        payloads.append(
            _gateway_source_payload(gateway, gateway_observed_monotonic)
        )
    return payloads


def _external_source_payload(
    poll: _SourcePayloadPollPort,
    balance_sources: Mapping[str, Mapping[str, object]],
    control_sources: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    payload = poll.payload()
    source_id = poll.snapshot.source_id
    payload.update(balance_sources.get(source_id, {}))
    payload.update(control_sources.get(source_id, {}))
    return payload


def _gateway_source_payload(
    gateway: EnergySourceSnapshot,
    observed_monotonic: float | None,
) -> dict[str, object]:
    payload = dict(gateway.as_dict())
    payload.update(
        {
            "contributing": True,
            "poll_status": "semantic_gateway",
            "measurement_status": "fresh",
            "attempted_at": None,
            "observed_at": gateway.captured_at,
            "observed_monotonic": observed_monotonic,
            "next_poll_at": 0.0,
            "age_seconds": None,
            "consecutive_failures": 0,
            "last_error": "",
        }
    )
    return payload


def _nested_mappings(value: object) -> dict[str, dict[str, object]]:
    if not _is_object_mapping(value):
        return {}
    return {
        str(key): dict(item)
        for key, item in value.items()
        if _is_string_object_mapping(item)
    }


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_string_object_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return _is_object_mapping(value) and all(isinstance(key, str) for key in value)


def _battery_payload(
    effective_soc: float | None,
    cluster: Mapping[str, object],
    forecast: Mapping[str, object],
    balance: Mapping[str, object],
    control: Mapping[str, object],
    sources: list[dict[str, object]],
    profiles: Mapping[str, EnergyLearningProfile],
) -> Snapshot:
    return {
        "battery_soc": effective_soc,
        "battery_combined_soc": cluster.get("combined_soc"),
        "battery_combined_usable_capacity_wh": cluster.get("combined_usable_capacity_wh"),
        "battery_combined_charge_power_w": cluster.get("combined_charge_power_w"),
        "battery_combined_discharge_power_w": cluster.get("combined_discharge_power_w"),
        "battery_combined_net_power_w": cluster.get("combined_net_battery_power_w"),
        "battery_combined_ac_power_w": cluster.get("combined_ac_power_w"),
        "battery_combined_pv_input_power_w": cluster.get("combined_pv_input_power_w"),
        "battery_combined_grid_interaction_w": cluster.get("combined_grid_interaction_w"),
        "battery_headroom_charge_w": forecast.get("battery_headroom_charge_w"),
        "battery_headroom_discharge_w": forecast.get("battery_headroom_discharge_w"),
        "expected_near_term_export_w": forecast.get("expected_near_term_export_w"),
        "expected_near_term_import_w": forecast.get("expected_near_term_import_w"),
        "battery_discharge_balance_mode": balance.get("mode"),
        "battery_discharge_balance_target_distribution_mode": balance.get("target_distribution_mode"),
        "battery_discharge_balance_error_w": balance.get("error_w"),
        "battery_discharge_balance_max_abs_error_w": balance.get("max_abs_error_w"),
        "battery_discharge_balance_total_discharge_w": balance.get("total_discharge_w"),
        "battery_discharge_balance_eligible_source_count": balance.get("eligible_source_count", 0),
        "battery_discharge_balance_active_source_count": balance.get("active_source_count", 0),
        "battery_discharge_balance_control_candidate_count": control.get("control_candidate_count", 0),
        "battery_discharge_balance_control_ready_count": control.get("control_ready_count", 0),
        "battery_discharge_balance_supported_control_source_count": control.get("supported_control_source_count", 0),
        "battery_discharge_balance_experimental_control_source_count": control.get(
            "experimental_control_source_count",
            0,
        ),
        "battery_average_confidence": cluster.get("average_confidence"),
        "battery_source_count": cluster.get("source_count", 0),
        "battery_online_source_count": cluster.get("online_source_count", 0),
        "battery_valid_soc_source_count": cluster.get("valid_soc_source_count", 0),
        "battery_battery_source_count": cluster.get("battery_source_count", 0),
        "battery_hybrid_inverter_source_count": cluster.get("hybrid_inverter_source_count", 0),
        "battery_inverter_source_count": cluster.get("inverter_source_count", 0),
        "battery_sources": sources,
        "battery_learning_profiles": {
            source_id: profile.as_dict()
            for source_id, profile in profiles.items()
        },
    }


__all__ = ["ConfiguredEnergySources", "EnergySourceStepReader"]
