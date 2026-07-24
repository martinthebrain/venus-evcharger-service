# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for configured energy sources inside the input helper."""

from __future__ import annotations

import subprocess
import unittest
from dataclasses import replace
from typing import TypeGuard
from unittest.mock import MagicMock, patch

from tests.support.auto_input_helper import FakeEnergyGateway, helper_settings
from venus_evcharger.energy.models import (
    EnergyClusterSnapshot,
    EnergySourceDefinition,
    EnergySourceSnapshot,
)
from venus_evcharger.energy.read_steps import EnergySourceReadStep, completed_read
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalEnergyCycle,
    ExternalPollingPolicy,
    ExternalSourcePoll,
    PvProjectionPolicy,
)
from venus_evcharger.inputs.helper.external_pv_projection import (
    aggregate_pv_projection,
    external_pv_projection,
    pv_observation_times,
    single_pv_projection,
    usable_pv_poll,
)
from venus_evcharger.inputs.helper.external_sources import (
    ConfiguredEnergySources,
    _combined_soc_selection,
    _first_external_soc_source,
    _gateway_battery_source,
    _gateway_soc_source,
    _nested_mappings,
    _oldest_weighted_soc_observation,
    _selected_soc,
    _weighted_soc_observed_at,
)
from venus_evcharger.inputs.helper.sources import AutoInputSources
from venus_evcharger.ipc.energy import MeasuredValue


class _Clock:
    def __init__(self, current: float = 0.0) -> None:
        self.current = current

    def __call__(self) -> float:
        return self.current


class _SequenceReader:
    def __init__(self, results: list[EnergySourceSnapshot | Exception]) -> None:
        self.results = results
        self.source_ids: list[str] = []
        self.timeout_limits: list[float] = []

    def __call__(
        self,
        owner: object,
        source: EnergySourceDefinition,
        now: float,
    ) -> EnergySourceReadStep:
        limiter = getattr(owner, "bounded_request_timeout_seconds")
        self.timeout_limits.append(float(limiter(99.0)))
        self.source_ids.append(source.source_id)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        self.asserted_now = now
        return completed_read(result)


class _OwnedSession:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _definition(source_id: str = "external") -> EnergySourceDefinition:
    return EnergySourceDefinition(
        source_id=source_id,
        role="hybrid-inverter",
        connector_type="command_json",
        config_path=f"/{source_id}.ini",
    )


def _online_source(
    soc: float,
    now: float,
    *,
    source_id: str = "external",
    capacity_wh: float | None = None,
    pv_power_w: float | None = None,
) -> EnergySourceSnapshot:
    return EnergySourceSnapshot(
        source_id=source_id,
        role="hybrid-inverter",
        service_name=f"{source_id}-helper",
        soc=soc,
        usable_capacity_wh=capacity_wh,
        pv_input_power_w=pv_power_w,
        online=True,
        confidence=0.75,
        captured_at=now,
    )


def _policy(*, stale_age: float = 5.0) -> ExternalPollingPolicy:
    return ExternalPollingPolicy(
        poll_interval_seconds=1.0,
        backoff_base_seconds=1.0,
        backoff_max_seconds=2.0,
        last_good_max_age_seconds=stale_age,
        cycle_budget_seconds=2.0,
    )


def _sources(
    reader: _SequenceReader,
    clock: _Clock,
    *,
    use_combined_soc: bool = False,
    stale_age: float = 5.0,
) -> ConfiguredEnergySources:
    return ConfiguredEnergySources(
        (_definition(),),
        use_combined_soc=use_combined_soc,
        request_timeout_seconds=1.25,
        polling_policy=_policy(stale_age=stale_age),
        pv_policy=PvProjectionPolicy(),
        reader=reader,
        monotonic=clock,
    )


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return _is_object_dict(value) and all(isinstance(key, str) for key in value)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _source_payload(cycle: ExternalEnergyCycle, index: int) -> dict[str, object]:
    battery = cycle.battery
    raw_sources = battery["battery_sources"]
    assert _is_object_list(raw_sources)
    payload = raw_sources[index]
    assert _is_string_object_dict(payload)
    return payload


def _poll(
    snapshot: EnergySourceSnapshot,
    *,
    contributing: bool = True,
    observed_at: float | None = 10.0,
) -> ExternalSourcePoll:
    return ExternalSourcePoll(
        snapshot=snapshot,
        contributing=contributing,
        poll_status="success",
        measurement_status="fresh",
        attempted_at=observed_at,
        observed_at=observed_at,
        next_poll_at=11.0,
        age_seconds=0.0,
        consecutive_failures=0,
        last_error="",
    )


class ConfiguredEnergySourceContracts(unittest.TestCase):
    def test_constructor_wires_scheduler_and_owned_state_exactly_once(self) -> None:
        definition = _definition()
        policy = _policy()
        pv_policy = PvProjectionPolicy(name="external_only", external_source_id="external")
        session = object()
        reader = MagicMock()
        clock = _Clock(4.0)
        with patch(
            "venus_evcharger.inputs.helper.external_sources.ExternalSourceScheduler"
        ) as scheduler_type:
            sources = ConfiguredEnergySources(
                (definition,),
                use_combined_soc=True,
                request_timeout_seconds=1.25,
                polling_policy=policy,
                pv_policy=pv_policy,
                session=session,
                reader=reader,
                monotonic=clock,
            )

        scheduler_type.assert_called_once_with(
            (definition,),
            policy,
            1.25,
            reader,
            session=session,
            monotonic=clock,
        )
        self.assertEqual(sources.gateway_source_id, "victron")
        self.assertIsNone(sources.gateway_definition)
        self.assertIs(sources.pv_policy, pv_policy)
        self.assertEqual(sources._learning_profiles, {})

    def test_owned_http_session_is_reused_across_polls_and_closed_once(self) -> None:
        owned = _OwnedSession()
        observed_sessions: list[object | None] = []

        def reader(
            runtime: object,
            source: EnergySourceDefinition,
            now: float,
        ) -> EnergySourceReadStep:
            observed_sessions.append(getattr(runtime, "session"))
            return completed_read(_online_source(50.0, now, source_id=source.source_id))

        clock = _Clock()
        with patch(
            "venus_evcharger.energy.http_session.requests.Session",
            return_value=owned,
        ) as session_type:
            sources = ConfiguredEnergySources(
                (_definition(),),
                use_combined_soc=False,
                request_timeout_seconds=1.0,
                polling_policy=_policy(),
                pv_policy=PvProjectionPolicy(),
                reader=reader,
                monotonic=clock,
            )

        sources.collect_cycle(None, 10.0)
        clock.current = 1.0
        sources.collect_cycle(None, 11.0)
        sources.close()
        sources.close()

        session_type.assert_called_once_with()
        self.assertEqual(observed_sessions, [owned, owned])
        self.assertEqual(owned.close_calls, 1)

    def test_gateway_and_external_views_of_same_battery_do_not_double_weight_soc(
        self,
    ) -> None:
        external_definition = replace(
            _definition(),
            physical_id="house-battery",
            usable_capacity_wh=5000.0,
        )
        gateway_definition = EnergySourceDefinition(
            source_id="victron",
            physical_id="house-battery",
            usable_capacity_wh=5000.0,
        )
        reader = _SequenceReader(
            [
                replace(
                    _online_source(80.0, 100.0, capacity_wh=5000.0),
                    physical_id="house-battery",
                )
            ]
        )
        sources = ConfiguredEnergySources(
            (external_definition,),
            use_combined_soc=True,
            request_timeout_seconds=1.0,
            polling_policy=_policy(),
            pv_policy=PvProjectionPolicy(),
            gateway_definition=gateway_definition,
            reader=reader,
            monotonic=_Clock(),
        )

        cycle = sources.collect_cycle(
            MeasuredValue(60.0, 99.0, "fresh", 1.0, ("battery",)),
            100.0,
        )

        self.assertEqual(cycle.battery["battery_soc"], 80.0)
        self.assertEqual(cycle.battery["battery_combined_soc"], 80.0)
        self.assertEqual(
            cycle.battery["battery_combined_usable_capacity_wh"],
            5000.0,
        )
        self.assertEqual(cycle.battery["battery_valid_soc_source_count"], 1)
        self.assertEqual(cycle.battery_observed_at, 100.0)

    def test_failures_are_coalesced_and_recovery_restores_external_soc(self) -> None:
        failure = subprocess.CalledProcessError(1, ("energy-helper",))
        reader = _SequenceReader([failure, failure, _online_source(44.0, 13.0)])
        clock = _Clock()
        sources = _sources(reader, clock)
        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning") as warning, patch(
            "venus_evcharger.inputs.helper.external_scheduler.logging.info"
        ) as info:
            first = sources.collect_cycle(None, 10.0)
            clock.current = 1.0
            second = sources.collect_cycle(None, 11.0)
            clock.current = 3.0
            recovered = sources.collect_cycle(None, 13.0)

        self.assertIsNone(first.battery["battery_soc"])
        self.assertIsNone(second.battery["battery_soc"])
        self.assertFalse(_source_payload(first, 0)["online"])
        self.assertEqual(recovered.battery["battery_soc"], 44.0)
        self.assertTrue(_source_payload(recovered, 0)["online"])
        self.assertEqual(reader.source_ids, ["external", "external", "external"])
        self.assertEqual(reader.timeout_limits, [1.25, 1.25, 1.25])
        warning.assert_called_once()
        info.assert_called_once()

    def test_gateway_soc_is_fallback_and_offline_source_does_not_create_learning(self) -> None:
        reader = _SequenceReader([OSError("offline")])
        sources = _sources(reader, _Clock(), use_combined_soc=True)
        gateway = MeasuredValue(61.0, 20.0, "fresh", 0.8)

        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning"):
            cycle = sources.collect_cycle(gateway, 20.0)

        self.assertEqual(cycle.battery["battery_soc"], 61.0)
        self.assertEqual(cycle.battery_observed_at, 20.0)
        self.assertEqual(cycle.battery["battery_source_count"], 1)
        self.assertEqual(_source_payload(cycle, 1)["service_name"], "semantic-gateway")
        self.assertEqual(cycle.battery["battery_learning_profiles"], {})

    def test_failed_poll_preserves_last_good_observation_without_refreshing_freshness(self) -> None:
        reader = _SequenceReader(
            [
                _online_source(44.0, 10.0, capacity_wh=10000.0),
                OSError("offline"),
                OSError("still offline"),
            ]
        )
        clock = _Clock()
        sources = _sources(reader, clock, use_combined_soc=True, stale_age=5.0)

        fresh = sources.collect_cycle(None, 10.0)
        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning"):
            clock.current = 1.0
            stale = sources.collect_cycle(None, 11.0)
            clock.current = 6.1
            expired = sources.collect_cycle(None, 16.1)

        self.assertEqual(fresh.battery_observed_at, 10.0)
        self.assertEqual(stale.battery["battery_soc"], 44.0)
        self.assertEqual(stale.battery_observed_at, 10.0)
        stale_payload = _source_payload(stale, 0)
        self.assertEqual(stale_payload["attempted_at"], 11.0)
        self.assertEqual(stale_payload["observed_at"], 10.0)
        self.assertEqual(stale_payload["captured_at"], 10.0)
        self.assertEqual(stale_payload["measurement_status"], "stale")
        self.assertEqual(stale_payload["poll_status"], "failed")
        self.assertTrue(stale_payload["contributing"])
        self.assertIsNone(expired.battery["battery_soc"])
        self.assertIsNone(expired.battery_observed_at)
        self.assertEqual(_source_payload(expired, 0)["measurement_status"], "expired")
        learning = expired.battery["battery_learning_profiles"]
        assert _is_string_object_dict(learning)
        external_learning = learning["external"]
        assert _is_string_object_dict(external_learning)
        self.assertEqual(external_learning["sample_count"], 1)

    def test_nested_source_metrics_accept_only_mapping_members(self) -> None:
        self.assertEqual(_nested_mappings(None), {})
        self.assertEqual(
            _nested_mappings(
                {
                    "valid": {"metric": 1},
                    "non_string_metric": {1: "invalid"},
                    "invalid": 2,
                }
            ),
            {"valid": {"metric": 1}},
        )

    def test_pv_projection_can_select_one_source_or_scope_aware_aggregate(self) -> None:
        first = EnergySourceSnapshot(
            source_id="first",
            role="inverter",
            service_name="first",
            pv_input_power_w=100.0,
            pv_input_power_scope_key="first",
            online=True,
            confidence=0.8,
            captured_at=9.0,
        )
        second = EnergySourceSnapshot(
            source_id="second",
            role="inverter",
            service_name="second",
            pv_input_power_w=200.0,
            pv_input_power_scope_key="second",
            online=True,
            confidence=0.6,
            captured_at=10.0,
        )
        polls = (
            _poll(first, observed_at=9.0),
            _poll(second, observed_at=10.0),
        )

        selected = external_pv_projection(polls, "second")
        aggregate = external_pv_projection(polls, "")
        assert selected is not None
        assert aggregate is not None
        self.assertEqual((selected.value, selected.source_id), (200.0, "second"))
        self.assertEqual(aggregate.value, 300.0)
        self.assertEqual(aggregate.observed_at, 9.0)
        self.assertEqual(aggregate.source_id, "external-aggregate")
        self.assertEqual(aggregate.confidence, 0.6)
        self.assertIsNone(external_pv_projection(polls, "missing"))

    def test_pv_and_soc_projection_defensive_boundaries_are_explicit(self) -> None:
        empty = EnergySourceSnapshot(
            source_id="empty",
            role="inverter",
            service_name="empty",
            online=True,
            captured_at=10.0,
        )
        pv_without_observation = EnergySourceSnapshot(
            source_id="pv",
            role="inverter",
            service_name="pv",
            pv_input_power_w=100.0,
            online=True,
            captured_at=10.0,
        )
        self.assertIsNone(single_pv_projection(_poll(empty)))
        self.assertIsNone(aggregate_pv_projection((_poll(empty),)))
        self.assertIsNone(
            aggregate_pv_projection((_poll(pv_without_observation, observed_at=None),))
        )
        self.assertIsNone(_weighted_soc_observed_at(empty))
        self.assertIsNone(
            _weighted_soc_observed_at(
                EnergySourceSnapshot(
                    source_id="soc",
                    role="battery",
                    service_name="soc",
                    soc=50.0,
                    usable_capacity_wh=0.0,
                    captured_at=10.0,
                )
            )
        )

        invalid = EnergySourceSnapshot(
            source_id="invalid",
            role="battery",
            service_name="invalid",
            soc=None,
            online=False,
            captured_at=None,
        )
        valid = _online_source(42.0, 8.0, source_id="valid")
        value, observed_at = _selected_soc(
            EnergyClusterSnapshot(),
            (invalid, valid),
            None,
            False,
        )
        self.assertEqual((value, observed_at), (42.0, 8.0))
        self.assertEqual(_selected_soc(EnergyClusterSnapshot(), (), None, False), (None, None))

    def test_gateway_projection_preserves_source_metadata_and_precedence(self) -> None:
        definition = EnergySourceDefinition(
            source_id="configured",
            role="battery",
            connector_type="command_json",
            config_path="/configured.ini",
            service_name="configured-service",
            usable_capacity_wh=1234.0,
            battery_chemistry="nmc",
        )
        measured = MeasuredValue(57.0, 12.0, "fresh", 0.4, ("bus-a", "bus-b"))
        expected = EnergySourceSnapshot(
            source_id="configured",
            role="battery",
            service_name="bus-a,bus-b",
            soc=57.0,
            usable_capacity_wh=1234.0,
            battery_chemistry="nmc",
            online=True,
            confidence=0.4,
            captured_at=12.0,
        )

        self.assertEqual(_gateway_battery_source(measured, "fallback", definition), expected)
        configured_service = replace(measured, source_ids=())
        self.assertEqual(
            _gateway_battery_source(configured_service, "fallback", definition),
            replace(expected, service_name="configured-service"),
        )
        unconfigured = _gateway_battery_source(configured_service, "fallback", None)
        self.assertEqual(
            unconfigured,
            replace(
                expected,
                source_id="fallback",
                service_name="semantic-gateway",
                usable_capacity_wh=None,
                battery_chemistry="",
            ),
        )
        self.assertIsNone(_gateway_battery_source(None, "fallback", definition))
        self.assertIsNone(
            _gateway_battery_source(
                MeasuredValue(None, 0.0, "unknown", 0.0),
                "fallback",
                definition,
            )
        )

    def test_soc_projection_uses_oldest_weighted_observation_and_strict_fallbacks(self) -> None:
        early = _online_source(40.0, 7.0, source_id="early", capacity_wh=1000.0)
        late = _online_source(60.0, 9.0, source_id="late", capacity_wh=2000.0)
        gateway = EnergySourceSnapshot(
            source_id="gateway",
            role="battery",
            service_name="gateway",
            soc=50.0,
            usable_capacity_wh=3000.0,
            online=True,
            captured_at=6.0,
        )
        cluster = EnergyClusterSnapshot(combined_soc=52.5)

        self.assertEqual(_selected_soc(cluster, (late, early), gateway, True), (52.5, 6.0))
        self.assertEqual(_combined_soc_selection(52.5, (late, early), gateway), (52.5, 6.0))
        self.assertEqual(_oldest_weighted_soc_observation((late, early), gateway), 6.0)
        self.assertIs(_first_external_soc_source((late, early)), late)
        offline = replace(early, source_id="offline", online=False)
        self.assertIs(_first_external_soc_source((offline, late)), late)
        self.assertIs(_gateway_soc_source(gateway), gateway)
        self.assertIsNone(_gateway_soc_source(replace(gateway, captured_at=None)))
        self.assertEqual(_weighted_soc_observed_at(replace(early, usable_capacity_wh=0.5)), 7.0)
        self.assertIsNone(_weighted_soc_observed_at(replace(early, soc=None)))
        self.assertIsNone(_weighted_soc_observed_at(replace(early, usable_capacity_wh=None)))
        self.assertIsNone(_weighted_soc_observed_at(replace(early, captured_at=None)))

    def test_pv_poll_contract_rejects_each_non_contributing_boundary(self) -> None:
        source = _online_source(50.0, 10.0, source_id="pv", pv_power_w=0.0)
        poll = _poll(source)

        self.assertTrue(usable_pv_poll(poll, "pv"))
        self.assertTrue(usable_pv_poll(poll, ""))
        self.assertFalse(usable_pv_poll(replace(poll, contributing=False), "pv"))
        self.assertFalse(usable_pv_poll(poll, "other"))
        self.assertFalse(usable_pv_poll(_poll(replace(source, pv_input_power_w=None)), "pv"))
        self.assertFalse(usable_pv_poll(_poll(replace(source, pv_input_power_w=-0.1)), "pv"))
        self.assertEqual(pv_observation_times((poll, replace(poll, observed_at=None))), (10.0,))
        projection = single_pv_projection(poll)
        assert projection is not None
        self.assertEqual(
            (projection.value, projection.observed_at, projection.source_id, projection.confidence),
            (0.0, 10.0, "pv", 0.75),
        )
        stale = replace(poll, measurement_status="stale")
        aggregated = aggregate_pv_projection((poll, stale))
        assert aggregated is not None
        self.assertEqual(aggregated.measurement_status, "stale")

    def test_invalid_gateway_soc_requests_refresh_while_external_source_remains_usable(self) -> None:
        settings = replace(
            helper_settings(),
            energy_sources=(_definition(),),
            external_polling_policy=_policy(),
        )
        gateway = FakeEnergyGateway()
        gateway.measurements["battery"] = MeasuredValue(120.0, 99.0, "fresh", 1.0)
        reader = _SequenceReader([_online_source(42.0, 100.0)])
        sources = AutoInputSources(settings, gateway, energy_source_reader=reader)

        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            sources.prepare_cycle()
            snapshot = sources.battery_snapshot()

        self.assertEqual(snapshot["battery_soc"], 42.0)
        self.assertEqual(gateway.requests[0][0], "battery")


if __name__ == "__main__":
    unittest.main()
