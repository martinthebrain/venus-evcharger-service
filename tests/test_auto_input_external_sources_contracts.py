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
from venus_evcharger.energy.read_steps import pending_read
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalEnergyCycle,
    ExternalPollingPolicy,
    ExternalSourcePoll,
    ProjectedEnergyValue,
    PvProjectionPolicy,
    projection_measurement_status,
)
from venus_evcharger.inputs.helper.external_pv_projection import (
    aggregate_pv_projection,
    external_pv_projection,
    pv_observation_times,
    single_pv_projection,
    usable_pv_poll,
)
from venus_evcharger.inputs.helper.external_scheduler import (
    EnergyConnectorRuntime,
    ExternalSourceScheduler,
    _SourceState,
    _backoff_seconds,
    _configured_snapshot,
    _confirms_measurement,
    _has_contributing_value,
    _measurement_status,
    _offline_source,
    _poll_status,
    _snapshot_age,
    _valid_observed_at,
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
        observed_at: float,
    ) -> EnergySourceReadStep:
        limiter = getattr(owner, "bounded_request_timeout_seconds")
        self.timeout_limits.append(float(limiter(99.0)))
        self.source_ids.append(source.source_id)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        self.asserted_now = observed_at
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
    definition: EnergySourceDefinition | None = None,
) -> ConfiguredEnergySources:
    return ConfiguredEnergySources(
        (definition or _definition(),),
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


class ExternalSourceSchedulerMutationContracts(unittest.TestCase):
    def test_connector_runtime_bounds_timeout_by_configuration_and_deadline(self) -> None:
        clock = _Clock(10.0)
        session = object()
        runtime = EnergyConnectorRuntime(0.5, session, clock)

        self.assertEqual(runtime.shelly_request_timeout_seconds, 0.5)
        self.assertIs(runtime.session, session)
        self.assertEqual(runtime.bounded_request_timeout_seconds(-1.0), 0.001)
        self.assertEqual(runtime.bounded_request_timeout_seconds(0.2), 0.2)
        self.assertEqual(runtime.bounded_request_timeout_seconds(2.0), 0.5)

        runtime.begin_attempt(10.25)
        self.assertAlmostEqual(runtime.bounded_request_timeout_seconds(2.0), 0.25)
        clock.current = 10.1
        self.assertAlmostEqual(runtime.bounded_request_timeout_seconds(2.0), 0.15)
        clock.current = 10.3
        self.assertEqual(runtime.bounded_request_timeout_seconds(2.0), 0.001)

    def test_round_robin_pending_reads_are_resumed_fairly(self) -> None:
        clock = _Clock(4.0)
        calls: list[str] = []

        def reader(
            owner: object,
            source: EnergySourceDefinition,
            observed_at: float,
        ) -> EnergySourceReadStep:
            del owner, observed_at
            calls.append(source.source_id)
            return pending_read()

        scheduler = ExternalSourceScheduler(
            tuple(_definition(source_id) for source_id in ("a", "b", "c")),
            _policy(),
            1.0,
            reader,
            monotonic=clock,
        )

        cycles = tuple(scheduler.poll(100.0) for _ in range(4))

        self.assertEqual(calls, ["a", "b", "c", "a"])
        self.assertEqual(
            tuple(tuple(poll.poll_status for poll in cycle) for cycle in cycles),
            (
                ("in_progress", "deferred_budget", "deferred_budget"),
                ("in_progress", "in_progress", "deferred_budget"),
                ("in_progress", "in_progress", "in_progress"),
                ("in_progress", "in_progress", "in_progress"),
            ),
        )
        first = cycles[0][0]
        self.assertEqual(first.attempted_at, 100.0)
        self.assertEqual(first.next_poll_at, 100.0)
        self.assertEqual(first.measurement_status, "missing")
        self.assertEqual(first.consecutive_failures, 0)
        self.assertEqual(first.last_error, "")

    def test_failure_backoff_and_recovery_publish_exact_diagnostics(self) -> None:
        clock = _Clock(10.0)
        results: list[EnergySourceSnapshot | Exception] = [
            OSError("network down"),
            _online_source(52.0, 102.0, source_id="connector-id"),
        ]

        def reader(
            owner: object,
            source: EnergySourceDefinition,
            observed_at: float,
        ) -> EnergySourceReadStep:
            del owner, observed_at
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            self.assertEqual(source.source_id, "configured")
            return completed_read(result)

        definition = replace(
            _definition("configured"),
            role="battery",
            physical_id="physical",
            physical_priority=7,
        )
        scheduler = ExternalSourceScheduler(
            (definition,),
            _policy(),
            1.0,
            reader,
            monotonic=clock,
        )
        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning") as warning, patch(
            "venus_evcharger.inputs.helper.external_scheduler.logging.info"
        ) as info:
            failed = scheduler.poll(100.0)[0]
            clock.current = 10.5
            backed_off = scheduler.poll(100.5)[0]
            clock.current = 11.0
            recovered = scheduler.poll(102.0)[0]

        self.assertEqual(failed.poll_status, "failed")
        self.assertEqual(failed.measurement_status, "missing")
        self.assertEqual(failed.attempted_at, 100.0)
        self.assertIsNone(failed.observed_at)
        self.assertEqual(failed.next_poll_at, 101.0)
        self.assertIsNone(failed.age_seconds)
        self.assertEqual(failed.consecutive_failures, 1)
        self.assertEqual(failed.last_error, "network down")
        self.assertFalse(failed.snapshot.online)
        self.assertEqual(backed_off.poll_status, "backoff")
        self.assertEqual(backed_off.next_poll_at, 101.0)

        self.assertEqual(recovered.poll_status, "success")
        self.assertEqual(recovered.measurement_status, "fresh")
        self.assertTrue(recovered.contributing)
        self.assertEqual(recovered.snapshot.source_id, "configured")
        self.assertEqual(recovered.snapshot.role, "battery")
        self.assertEqual(recovered.snapshot.physical_id, "physical")
        self.assertEqual(recovered.snapshot.physical_priority, 7)
        self.assertEqual(recovered.observed_at, 102.0)
        self.assertEqual(recovered.next_poll_at, 103.0)
        self.assertEqual(recovered.age_seconds, 0.0)
        self.assertEqual(recovered.consecutive_failures, 0)
        self.assertEqual(recovered.last_error, "")
        self.assertIs(scheduler._states["configured"].in_progress, False)
        self.assertEqual(scheduler._failed_sources, set())
        warning.assert_called_once_with(
            "External energy source unavailable source=%s: %s",
            "configured",
            "network down",
        )
        info.assert_called_once_with(
            "External energy source recovered source=%s",
            "configured",
        )

    def test_repeated_failure_increments_backoff_and_clears_pending_state(self) -> None:
        clock = _Clock(10.0)

        def reader(
            owner: object,
            source: EnergySourceDefinition,
            observed_at: float,
        ) -> EnergySourceReadStep:
            del owner, source, observed_at
            raise OSError("still unavailable")

        scheduler = ExternalSourceScheduler(
            (_definition("source"),),
            _policy(),
            1.0,
            reader,
            monotonic=clock,
        )
        scheduler._states["source"].in_progress = True
        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning") as warning:
            first = scheduler.poll(100.0)[0]
            clock.current = 11.0
            second = scheduler.poll(101.0)[0]

        self.assertEqual(first.consecutive_failures, 1)
        self.assertEqual(first.next_poll_at, 101.0)
        self.assertEqual(second.consecutive_failures, 2)
        self.assertEqual(second.next_poll_at, 103.0)
        self.assertEqual(second.last_error, "still unavailable")
        self.assertIs(scheduler._states["source"].in_progress, False)
        warning.assert_called_once()

    def test_invalid_completed_measurement_uses_the_normal_failure_contract(self) -> None:
        clock = _Clock(7.0)
        snapshots = iter(
            (
                EnergySourceSnapshot(
                    source_id="connector",
                    role="battery",
                    service_name="connector",
                    soc=50.0,
                    online=False,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="connector",
                    role="battery",
                    service_name="connector",
                    online=True,
                    captured_at=101.0,
                ),
            )
        )

        def reader(
            owner: object,
            source: EnergySourceDefinition,
            observed_at: float,
        ) -> EnergySourceReadStep:
            del owner, source, observed_at
            return completed_read(next(snapshots))

        scheduler = ExternalSourceScheduler(
            (_definition("configured"),),
            _policy(),
            1.0,
            reader,
            monotonic=clock,
        )
        with patch("venus_evcharger.inputs.helper.external_scheduler.logging.warning") as warning:
            offline = scheduler.poll(100.0)[0]
            clock.current = 8.0
            empty = scheduler.poll(101.0)[0]

        for poll in (offline, empty):
            self.assertEqual(poll.poll_status, "failed")
            self.assertEqual(poll.measurement_status, "missing")
            self.assertEqual(
                poll.last_error,
                "source reported no online measurement",
            )
        self.assertEqual(empty.consecutive_failures, 2)
        self.assertIs(scheduler._states["configured"].in_progress, False)
        warning.assert_called_once_with(
            "External energy source unavailable source=%s: %s",
            "configured",
            "source reported no online measurement",
        )

    def test_due_selection_skips_blocked_source_in_forward_cursor_order(self) -> None:
        scheduler = ExternalSourceScheduler(
            tuple(_definition(source_id) for source_id in ("a", "b", "c")),
            _policy(),
            1.0,
            MagicMock(),
            monotonic=_Clock(),
        )
        scheduler._cursor = 1
        scheduler._states["a"].next_poll_monotonic = 0.0
        scheduler._states["b"].next_poll_monotonic = 2.0
        scheduler._states["c"].next_poll_monotonic = 0.0

        selected = scheduler._next_due_definition(1.0)

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.source_id, "c")
        self.assertEqual(scheduler._cursor, 0)

    def test_last_good_freshness_includes_the_configured_age_boundary(self) -> None:
        scheduler = ExternalSourceScheduler(
            (_definition("source"),),
            _policy(stale_age=5.0),
            1.0,
            MagicMock(),
            monotonic=_Clock(10.0),
        )
        state = scheduler._states["source"]
        state.last_good = _online_source(50.0, 95.0, source_id="source")
        state.next_poll_monotonic = 20.0

        boundary = scheduler.poll(100.0)[0]
        expired = scheduler.poll(100.001)[0]

        self.assertTrue(boundary.contributing)
        self.assertEqual(boundary.age_seconds, 5.0)
        self.assertEqual(boundary.measurement_status, "fresh")
        self.assertFalse(expired.contributing)
        self.assertAlmostEqual(expired.age_seconds or 0.0, 5.001)
        self.assertEqual(expired.measurement_status, "expired")

    def test_scheduler_primitives_define_all_boundaries_exactly(self) -> None:
        policy = ExternalPollingPolicy(
            poll_interval_seconds=1.0,
            backoff_base_seconds=2.0,
            backoff_max_seconds=10.0,
            last_good_max_age_seconds=5.0,
            cycle_budget_seconds=1.0,
        )
        self.assertEqual(
            tuple(_backoff_seconds(policy, failures) for failures in (0, 1, 2, 3, 4, 31, 32)),
            (2.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0),
        )
        uncapped = ExternalPollingPolicy(
            backoff_base_seconds=1.0,
            backoff_max_seconds=float(2**31),
        )
        self.assertEqual(_backoff_seconds(uncapped, 32), float(2**30))
        validity_cases = (
            (None, 10.0, False),
            (-0.001, 10.0, False),
            (0.0, 10.0, True),
            (10.0, 10.0, True),
            (10.001, 10.0, False),
            (float("nan"), 10.0, False),
            (float("inf"), 10.0, False),
            (-float("inf"), 10.0, False),
        )
        for observed_at, now, expected in validity_cases:
            with self.subTest(observed_at=observed_at):
                self.assertIs(_valid_observed_at(observed_at, now), expected)

        state = _SourceState()
        self.assertEqual(_poll_status(state, 0.0, "success"), "success")
        state.in_progress = True
        self.assertEqual(_poll_status(state, 0.0, None), "in_progress")
        state.in_progress = False
        state.next_poll_monotonic = 1.0
        self.assertEqual(_poll_status(state, 1.0, None), "deferred_budget")
        state.next_poll_monotonic = 2.0
        state.consecutive_failures = 1
        self.assertEqual(_poll_status(state, 1.0, None), "backoff")
        state.consecutive_failures = 0
        self.assertEqual(_poll_status(state, 1.0, None), "idle")
        self.assertEqual(_measurement_status(state, None, False), "missing")
        self.assertEqual(_measurement_status(state, 5.001, False), "expired")
        state.consecutive_failures = 1
        self.assertEqual(_measurement_status(state, 5.0, True), "stale")
        state.consecutive_failures = 0
        self.assertEqual(_measurement_status(state, 0.0, True), "fresh")

    def test_measurement_confirmation_requires_online_time_and_one_value(self) -> None:
        empty = EnergySourceSnapshot(
            source_id="source",
            role="battery",
            service_name="service",
            online=True,
            captured_at=0.0,
        )
        self.assertFalse(_has_contributing_value(empty))
        self.assertFalse(_confirms_measurement(empty, 10.0))
        value_fields = (
            "soc",
            "usable_capacity_wh",
            "net_battery_power_w",
            "charge_limit_power_w",
            "discharge_limit_power_w",
            "ac_power_w",
            "pv_input_power_w",
            "grid_interaction_w",
        )
        for field in value_fields:
            snapshot = replace(empty, **{field: 0.0})
            with self.subTest(field=field):
                self.assertTrue(_has_contributing_value(snapshot))
                self.assertTrue(_confirms_measurement(snapshot, 10.0))
                self.assertFalse(_confirms_measurement(replace(snapshot, online=False), 10.0))
                self.assertFalse(_confirms_measurement(replace(snapshot, captured_at=10.001), 10.0))

    def test_snapshot_helpers_preserve_identity_age_and_service_fallback_order(self) -> None:
        connector = _online_source(50.0, 8.0, source_id="connector")
        definition = replace(
            _definition("configured"),
            role="battery",
            service_name="explicit",
            physical_id="physical",
            physical_priority=9,
        )
        configured = _configured_snapshot(definition, connector)
        self.assertEqual(configured.source_id, "configured")
        self.assertEqual(configured.role, "battery")
        self.assertEqual(configured.service_name, connector.service_name)
        self.assertEqual(configured.physical_id, "physical")
        self.assertEqual(configured.physical_priority, 9)
        self.assertEqual(_snapshot_age(configured, 10.0), 2.0)
        self.assertIsNone(_snapshot_age(configured, 7.999))
        self.assertIsNone(_snapshot_age(None, 10.0))

        explicit = _offline_source(definition)
        config_path = _offline_source(replace(definition, service_name=""))
        source_id = _offline_source(
            replace(definition, service_name="", config_path="")
        )
        self.assertEqual(explicit.service_name, "explicit")
        self.assertEqual(config_path.service_name, definition.config_path)
        self.assertEqual(source_id.service_name, "configured")
        self.assertEqual(explicit.source_id, "configured")
        self.assertEqual(explicit.role, "battery")
        self.assertEqual(explicit.physical_id, "physical")
        self.assertEqual(explicit.physical_priority, 9)


class AutoInputSourcesMutationContracts(unittest.TestCase):
    def test_constructor_passes_every_external_boundary_dependency_exactly(self) -> None:
        settings = helper_settings()
        gateway = FakeEnergyGateway()
        session = object()
        reader = MagicMock()
        external = MagicMock()
        with patch(
            "venus_evcharger.inputs.helper.sources.ConfiguredEnergySources",
            return_value=external,
        ) as external_type:
            sources = AutoInputSources(
                settings,
                gateway,
                connector_session=session,
                energy_source_reader=reader,
            )

        self.assertIs(sources.settings, settings)
        self.assertIs(sources.gateway, gateway)
        self.assertEqual(sources._measurements, {})
        self.assertIsNone(sources._gateway_battery)
        self.assertIsNone(sources._battery_observed_at)
        self.assertIsNone(sources._external_cycle)
        self.assertIsNone(sources._pv_projection)
        self.assertIs(sources.external, external)
        external_type.assert_called_once_with(
            settings.energy_sources,
            use_combined_soc=settings.use_combined_battery_soc,
            request_timeout_seconds=settings.energy_source_request_timeout_seconds,
            polling_policy=settings.external_polling_policy,
            pv_policy=settings.pv_projection_policy,
            gateway_source_id=settings.grid_fusion_config.backup_source_id,
            gateway_definition=settings.gateway_energy_source,
            session=session,
            reader=reader,
        )
        sources.close()
        external.close.assert_called_once_with()

    def test_disabled_external_cycle_preserves_exact_gateway_measurements(self) -> None:
        gateway = FakeEnergyGateway()
        gateway.measurements = {
            "pv": MeasuredValue(1200.0, 99.0, "stale", 0.75, ("pv",)),
            "grid": MeasuredValue(-100.0, 98.0, "fresh", 0.8, ("grid",)),
            "battery": MeasuredValue(50.0, 97.0, "fresh", 0.9, ("battery",)),
            "battery_power": None,
        }
        sources = AutoInputSources(helper_settings(), gateway)
        external = MagicMock()
        external.enabled = False
        sources.external = external

        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            sources.prepare_cycle()

        self.assertEqual(gateway.input_refreshes, 1)
        self.assertEqual(sources._measurements, gateway.measurements)
        self.assertEqual(sources._gateway_battery, gateway.measurements["battery"])
        self.assertEqual(sources._battery_observed_at, 97.0)
        self.assertEqual(
            sources._pv_projection,
            ProjectedEnergyValue(
                value=1200.0,
                observed_at=99.0,
                source_id=sources.settings.grid_fusion_config.backup_source_id,
                confidence=0.75,
                measurement_status="stale",
            ),
        )
        self.assertIsNone(sources._external_cycle)
        self.assertEqual(sources.battery_snapshot()["battery_source_count"], 1)
        external.collect_cycle.assert_not_called()

    def test_enabled_external_cycle_overrides_gateway_projection_coherently(self) -> None:
        gateway = FakeEnergyGateway()
        gateway.measurements = {
            "pv": MeasuredValue(100.0, 99.0, "fresh", 0.6, ("pv",)),
            "grid": None,
            "battery": MeasuredValue(40.0, 98.0, "fresh", 0.8, ("battery",)),
        }
        settings = replace(
            helper_settings(),
            pv_projection_policy=PvProjectionPolicy("external_preferred"),
        )
        sources = AutoInputSources(settings, gateway)
        external_pv = ProjectedEnergyValue(200.0, 100.0, "external", 0.9)
        cycle = ExternalEnergyCycle(
            battery={"battery_soc": 55.0},
            pv=external_pv,
            battery_observed_at=96.0,
            polls=(),
        )
        external = MagicMock()
        external.enabled = True
        external.collect_cycle.return_value = cycle
        sources.external = external

        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            sources.prepare_cycle()

        external.collect_cycle.assert_called_once_with(gateway.measurements["battery"], 100.0)
        self.assertIs(sources._external_cycle, cycle)
        self.assertEqual(sources._battery_observed_at, 96.0)
        self.assertIs(sources._pv_projection, external_pv)
        self.assertEqual(sources.battery_snapshot(), {"battery_soc": 55.0})
        self.assertEqual(gateway.requests, [])

    def test_enabled_gateway_preferred_cycle_retains_better_gateway_pv(self) -> None:
        gateway = FakeEnergyGateway()
        gateway.measurements = {
            "pv": MeasuredValue(100.0, 100.0, "fresh", 0.9, ("pv",)),
            "grid": None,
            "battery": None,
        }
        settings = replace(
            helper_settings(),
            pv_projection_policy=PvProjectionPolicy("gateway_preferred"),
        )
        sources = AutoInputSources(settings, gateway)
        external_pv = ProjectedEnergyValue(200.0, 99.0, "external", 0.6)
        cycle = ExternalEnergyCycle(
            battery={},
            pv=external_pv,
            battery_observed_at=None,
            polls=(),
        )
        external = MagicMock()
        external.enabled = True
        external.collect_cycle.return_value = cycle
        sources.external = external

        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            sources.prepare_cycle()

        self.assertEqual(
            sources._pv_projection,
            ProjectedEnergyValue(
                100.0,
                100.0,
                settings.grid_fusion_config.backup_source_id,
                0.9,
            ),
        )

    def test_gateway_measurement_boundaries_and_refresh_request_are_exact(self) -> None:
        gateway = FakeEnergyGateway()
        settings = helper_settings()
        sources = AutoInputSources(settings, gateway)
        values = (
            (-0.001, False),
            (0.0, True),
            (100.0, True),
            (100.001, False),
        )
        for value, valid in values:
            measurement = MeasuredValue(value, 100.0, "fresh", 0.5, ("battery",))
            sources._measurements["battery"] = measurement
            with self.subTest(value=value):
                self.assertIs(
                    sources._valid_battery_measurement(100.0),
                    measurement if valid else None,
                )

        boundary = MeasuredValue(
            12.0,
            100.0 - settings.gateway_max_age_seconds,
            "stale",
            0.4,
            ("grid",),
        )
        sources._measurements["grid"] = boundary
        self.assertIs(
            sources._valid_measurement("grid", 100.0),
            boundary,
        )
        self.assertIsNone(
            sources._valid_measurement("grid", 100.001),
        )
        for status in ("unknown", "unavailable", "error"):
            sources._measurements["grid"] = replace(boundary, status=status)
            with self.subTest(status=status):
                self.assertIsNone(sources._valid_measurement("grid", 100.0))
        sources._measurements["grid"] = replace(boundary, observed_at=100.001)
        self.assertIsNone(sources._valid_measurement("grid", 100.0))

        sources._request_missing("grid")
        self.assertEqual(
            gateway.requests[-1],
            ("grid", "semantic grid measurement unavailable", True),
        )


class ConfiguredEnergySourceContracts(unittest.TestCase):
    def test_external_polling_policy_enforces_each_named_numeric_boundary(self) -> None:
        defaults = {
            "poll_interval_seconds": 1.0,
            "backoff_base_seconds": 5.0,
            "backoff_max_seconds": 60.0,
            "last_good_max_age_seconds": 30.0,
            "cycle_budget_seconds": 2.0,
        }
        positive_fields = {
            "poll_interval_seconds": "poll interval",
            "backoff_base_seconds": "backoff base",
            "backoff_max_seconds": "backoff maximum",
            "cycle_budget_seconds": "cycle time budget",
        }
        for field, label in positive_fields.items():
            for invalid in (0.0, -0.001, float("nan"), float("inf"), -float("inf")):
                values = defaults | {field: invalid}
                if field == "backoff_max_seconds":
                    values["backoff_base_seconds"] = 0.001
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError) as error:
                    ExternalPollingPolicy(**values)
                self.assertEqual(
                    str(error.exception),
                    f"External energy-source {label} must be positive",
                )
            values = defaults | {field: 0.001}
            if field == "backoff_max_seconds":
                values["backoff_base_seconds"] = 0.001
            self.assertEqual(getattr(ExternalPollingPolicy(**values), field), 0.001)

        for invalid in (-0.001, float("nan"), float("inf"), -float("inf")):
            values = defaults | {"last_good_max_age_seconds": invalid}
            with self.subTest(invalid=invalid), self.assertRaises(ValueError) as error:
                ExternalPollingPolicy(**values)
            self.assertEqual(
                str(error.exception),
                "External energy-source last-good maximum age must be non-negative",
            )
        self.assertEqual(
            ExternalPollingPolicy(last_good_max_age_seconds=0.0).last_good_max_age_seconds,
            0.0,
        )

    def test_external_polling_policy_backoff_range_includes_equal_boundary(self) -> None:
        equal = ExternalPollingPolicy(
            backoff_base_seconds=5.0,
            backoff_max_seconds=5.0,
        )
        self.assertEqual(equal.backoff_base_seconds, equal.backoff_max_seconds)
        with self.assertRaises(ValueError) as error:
            ExternalPollingPolicy(
                backoff_base_seconds=5.0,
                backoff_max_seconds=4.999,
            )
        self.assertEqual(
            str(error.exception),
            "External energy-source backoff maximum must cover its base",
        )

    def test_projection_measurement_status_is_an_exact_closed_contract(self) -> None:
        self.assertEqual(projection_measurement_status("fresh"), "fresh")
        self.assertEqual(projection_measurement_status("stale"), "stale")
        for unsupported in ("", "missing", "FRESH"):
            with self.subTest(unsupported=unsupported), self.assertRaises(ValueError) as error:
                projection_measurement_status(unsupported)
            self.assertEqual(
                str(error.exception),
                f"Unsupported contributing measurement status: {unsupported}",
            )

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
            owner: object,
            source: EnergySourceDefinition,
            observed_at: float,
        ) -> EnergySourceReadStep:
            observed_sessions.append(getattr(owner, "session"))
            return completed_read(
                _online_source(50.0, observed_at, source_id=source.source_id)
            )

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
            physical_priority=10,
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
                    source_id="connector-alias",
                    role="battery",
                    physical_id="connector-physical-id",
                    physical_priority=-5,
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
        external_payload = _source_payload(cycle, 0)
        self.assertEqual(external_payload["source_id"], "external")
        self.assertEqual(external_payload["role"], "hybrid-inverter")
        self.assertEqual(external_payload["physical_id"], "house-battery")
        self.assertEqual(external_payload["physical_priority"], 10)

    def test_offline_snapshot_preserves_complete_configured_identity(self) -> None:
        definition = replace(
            _definition(),
            service_name="configured-service",
            usable_capacity_wh=7200.0,
            battery_chemistry="nmc",
            physical_id="configured-physical-id",
            physical_priority=17,
        )
        sources = _sources(
            _SequenceReader([OSError("offline")]),
            _Clock(),
            definition=definition,
        )

        cycle = sources.collect_cycle(None, 10.0)

        payload = _source_payload(cycle, 0)
        self.assertEqual(payload["source_id"], definition.source_id)
        self.assertEqual(payload["role"], definition.role)
        self.assertEqual(payload["service_name"], definition.service_name)
        self.assertEqual(payload["usable_capacity_wh"], definition.usable_capacity_wh)
        self.assertEqual(payload["battery_chemistry"], definition.battery_chemistry)
        self.assertIsNone(payload["captured_at"])
        self.assertEqual(payload["physical_id"], definition.physical_id)
        self.assertEqual(payload["physical_priority"], definition.physical_priority)

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

    def test_pv_poll_usability_requires_every_boundary_condition(self) -> None:
        usable = EnergySourceSnapshot(
            source_id="pv",
            role="inverter",
            service_name="pv",
            pv_input_power_w=0.0,
            online=True,
            confidence=0.75,
            captured_at=8.0,
        )
        base = _poll(usable, observed_at=8.0)
        self.assertTrue(usable_pv_poll(base, ""))
        self.assertTrue(usable_pv_poll(base, "pv"))
        self.assertFalse(usable_pv_poll(base, "other"))
        self.assertFalse(usable_pv_poll(replace(base, contributing=False), "pv"))
        self.assertFalse(usable_pv_poll(replace(base, snapshot=replace(usable, pv_input_power_w=None)), "pv"))
        self.assertFalse(usable_pv_poll(replace(base, snapshot=replace(usable, pv_input_power_w=-0.01)), "pv"))

    def test_single_pv_projection_preserves_every_semantic_field(self) -> None:
        snapshot = EnergySourceSnapshot(
            source_id="pv-a",
            role="inverter",
            service_name="pv-a",
            pv_input_power_w=123.5,
            online=True,
            confidence=0.625,
            captured_at=7.0,
        )
        fresh = single_pv_projection(_poll(snapshot, observed_at=8.5))
        stale = single_pv_projection(
            replace(
                _poll(snapshot, observed_at=8.5),
                measurement_status="stale",
            )
        )
        self.assertEqual(
            fresh,
            ProjectedEnergyValue(
                value=123.5,
                observed_at=8.5,
                source_id="pv-a",
                confidence=0.625,
                measurement_status="fresh",
            ),
        )
        self.assertEqual(
            stale,
            ProjectedEnergyValue(
                value=123.5,
                observed_at=8.5,
                source_id="pv-a",
                confidence=0.625,
                measurement_status="stale",
            ),
        )
        self.assertIsNone(single_pv_projection(_poll(replace(snapshot, pv_input_power_w=None))))
        self.assertIsNone(single_pv_projection(_poll(snapshot, observed_at=None)))

    def test_aggregate_pv_projection_has_exact_freshness_confidence_and_time_contract(self) -> None:
        first = EnergySourceSnapshot(
            source_id="first",
            role="inverter",
            service_name="first",
            pv_input_power_w=125.0,
            pv_input_power_scope_key="first",
            online=True,
            confidence=0.9,
            captured_at=8.0,
        )
        second = EnergySourceSnapshot(
            source_id="second",
            role="inverter",
            service_name="second",
            pv_input_power_w=75.0,
            pv_input_power_scope_key="second",
            online=True,
            confidence=0.4,
            captured_at=9.0,
        )
        polls = (
            _poll(first, observed_at=8.0),
            replace(_poll(second, observed_at=9.0), measurement_status="stale"),
        )
        self.assertEqual(pv_observation_times(polls), (8.0, 9.0))
        self.assertEqual(
            aggregate_pv_projection(polls),
            ProjectedEnergyValue(
                value=200.0,
                observed_at=8.0,
                source_id="external-aggregate",
                confidence=0.4,
                measurement_status="stale",
            ),
        )
        self.assertEqual(
            aggregate_pv_projection(
                (
                    _poll(first, observed_at=8.0),
                    _poll(second, observed_at=9.0),
                )
            ),
            ProjectedEnergyValue(
                value=200.0,
                observed_at=8.0,
                source_id="external-aggregate",
                confidence=0.4,
                measurement_status="fresh",
            ),
        )

    def test_projection_filters_before_selecting_or_aggregating(self) -> None:
        first = EnergySourceSnapshot(
            source_id="selected",
            role="inverter",
            service_name="first",
            pv_input_power_w=10.0,
            pv_input_power_scope_key="first",
            online=True,
            confidence=0.9,
            captured_at=1.0,
        )
        later = replace(
            first,
            service_name="later",
            pv_input_power_w=20.0,
            pv_input_power_scope_key="later",
            confidence=0.5,
            captured_at=2.0,
        )
        ignored = replace(
            first,
            source_id="ignored",
            service_name="ignored",
            pv_input_power_w=1000.0,
            pv_input_power_scope_key="ignored",
        )
        polls = (
            _poll(first, observed_at=1.0),
            _poll(later, observed_at=2.0),
            _poll(ignored, contributing=False, observed_at=3.0),
        )
        self.assertEqual(
            external_pv_projection(polls, "selected"),
            ProjectedEnergyValue(10.0, 1.0, "selected", 0.9, "fresh"),
        )
        self.assertEqual(
            external_pv_projection(polls, ""),
            ProjectedEnergyValue(30.0, 1.0, "external-aggregate", 0.5, "fresh"),
        )
        self.assertIsNone(external_pv_projection((), ""))

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
            physical_id="configured-physical",
            physical_priority=12,
        )
        measured = MeasuredValue(57.0, 12.0, "fresh", 0.4, ("bus-a", "bus-b"))
        expected = EnergySourceSnapshot(
            source_id="configured",
            role="battery",
            service_name="bus-a,bus-b",
            physical_id="configured-physical",
            physical_priority=12,
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
                physical_id="",
                physical_priority=0,
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
