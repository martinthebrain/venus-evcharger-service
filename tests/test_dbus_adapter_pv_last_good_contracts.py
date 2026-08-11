#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the bounded PV last-good continuity window."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    gateway_paths,
    install_read_responder,
)
from venus_evcharger.dbus_adapter.read.aggregate import AggregateState, AggregateStore
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.read.pv_last_good import (
    PV_TRANSIENT_HOLD_REASON,
    PvAggregateContinuity,
    PvLastGoodWindow,
)
from venus_evcharger.dbus_adapter.read.semantic import energy_inputs_snapshot

_SERVICE = "com.victronenergy.pvinverter.test"
_PATH = "/Ac/Power"
_MEMBER = (_SERVICE, _PATH)


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def monotonic(self) -> float:
        return self.value


class PvLastGoodWindowContractTests(unittest.TestCase):
    def test_policy_rejects_non_finite_and_out_of_range_values(self) -> None:
        for factor in (0.0, 1.0):
            with self.subTest(valid_factor=factor):
                PvLastGoodWindow(initial_factor=factor)
        for factor in (-0.01, 1.01, math.inf, math.nan):
            with self.subTest(factor=factor):
                with self.assertRaises(ValueError) as raised:
                    PvLastGoodWindow(initial_factor=factor)
                self.assertEqual(
                    str(raised.exception),
                    "PV last-good initial factor must be between zero and one",
                )
        PvLastGoodWindow(window_seconds=0.5)
        for duration in (0.0, -1.0, math.inf, math.nan):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError) as raised:
                    PvLastGoodWindow(window_seconds=duration)
                self.assertEqual(
                    str(raised.exception),
                    "PV last-good window must be finite and positive",
                )

    def test_timeout_starts_at_eighty_percent_and_decays_to_zero(self) -> None:
        clock = _Clock()
        window = PvLastGoodWindow(monotonic=clock.monotonic)
        self.assertEqual(window.record_confirmed(*_MEMBER, 1000.0), 1000.0)
        self.assertEqual(window.estimates((_MEMBER,)), ())

        window.record_error(*_MEMBER, TimeoutError("timed out"))
        initial = window.estimates((_MEMBER,))[0]
        self.assertEqual(initial.member, _MEMBER)
        self.assertEqual(initial.value, 800.0)
        self.assertEqual(initial.confidence, 0.8)
        self.assertEqual(initial.age_seconds, 0.0)

        clock.value = 102.5
        midpoint = window.estimates((_MEMBER,))[0]
        self.assertAlmostEqual(midpoint.value, 400.0)
        self.assertAlmostEqual(midpoint.confidence, 0.4)
        self.assertEqual(midpoint.age_seconds, 2.5)

        clock.value = 104.999
        self.assertGreater(window.estimates((_MEMBER,))[0].value, 0.0)
        clock.value = 105.0
        self.assertEqual(window.estimates((_MEMBER,)), ())

    def test_repeated_failures_do_not_restart_the_window(self) -> None:
        clock = _Clock()
        window = PvLastGoodWindow(monotonic=clock.monotonic)
        window.record_confirmed(*_MEMBER, 500.0)
        window.record_error(*_MEMBER, RuntimeError("NoReply"))
        clock.value = 104.0
        window.record_error(*_MEMBER, RuntimeError("no_reply"))
        self.assertAlmostEqual(window.estimates((_MEMBER,))[0].value, 80.0)
        clock.value = 105.0
        self.assertEqual(window.estimates((_MEMBER,)), ())

    def test_fresh_value_ends_hold_and_can_start_a_new_window(self) -> None:
        clock = _Clock()
        window = PvLastGoodWindow(monotonic=clock.monotonic)
        window.record_confirmed(*_MEMBER, 100.0)
        window.record_error(*_MEMBER, TimeoutError())
        clock.value = 101.0
        self.assertEqual(window.record_confirmed(*_MEMBER, "250"), 250.0)
        self.assertEqual(window.estimates((_MEMBER,)), ())
        window.record_error(*_MEMBER, RuntimeError("NoReply"))
        self.assertEqual(window.estimates((_MEMBER,))[0].value, 200.0)

    def test_invalid_value_non_transient_error_and_topology_change_invalidate(self) -> None:
        invalid_values = (object(), "bad", math.inf, -math.inf, math.nan)
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                window = PvLastGoodWindow(monotonic=lambda: 1.0)
                window.record_confirmed(*_MEMBER, 100.0)
                self.assertIsNone(window.record_confirmed(*_MEMBER, invalid))
                window.record_error(*_MEMBER, TimeoutError())
                self.assertEqual(window.estimates((_MEMBER,)), ())

        window = PvLastGoodWindow(monotonic=lambda: 1.0)
        window.record_confirmed(*_MEMBER, True)
        window.record_error(*_MEMBER, RuntimeError("invalid payload"))
        window.record_error(*_MEMBER, TimeoutError())
        self.assertEqual(window.estimates((_MEMBER,)), ())

        window.record_confirmed(*_MEMBER, 100.0)
        window.record_error(*_MEMBER, TimeoutError())
        window.retain_members(((_SERVICE, "/Changed"),))
        self.assertEqual(window.estimates(((_SERVICE, "/Changed"),)), ())

    def test_invalidation_and_estimation_are_member_local(self) -> None:
        clock = _Clock()
        other = ("com.victronenergy.pvinverter.other", _PATH)
        missing = ("com.victronenergy.pvinverter.missing", _PATH)
        window = PvLastGoodWindow(monotonic=clock.monotonic)
        window.record_confirmed(*_MEMBER, 100.0)
        window.record_confirmed(*other, 200.0)

        self.assertIsNone(window.record_confirmed(*_MEMBER, object()))
        window.record_error(*_MEMBER, TimeoutError())
        window.record_error(*other, TimeoutError())

        estimates = window.estimates((missing, _MEMBER, other))
        self.assertEqual(tuple(item.member for item in estimates), (other,))
        self.assertEqual(estimates[0].value, 160.0)

    def test_invalid_initial_value_and_non_transient_error_are_noops(self) -> None:
        window = PvLastGoodWindow(monotonic=lambda: 1.0)

        self.assertIsNone(window.record_confirmed(*_MEMBER, object()))
        window.record_error(*_MEMBER, RuntimeError("invalid payload"))

        self.assertEqual(window.estimates((_MEMBER,)), ())


class PvAggregateContinuityContractTests(unittest.TestCase):
    def _continuity(
        self,
        clock: _Clock,
    ) -> tuple[PvAggregateContinuity, MagicMock, MagicMock, AggregateStore]:
        discovery = MagicMock()
        cache = MagicMock()
        cache.services = {_SERVICE: {"status": "present"}}
        adapter = MagicMock()
        adapter.energy_discovery = discovery
        adapter.cache = cache
        aggregates = AggregateStore()
        continuity = PvAggregateContinuity(
            adapter,
            aggregates,
            monotonic=clock.monotonic,
        )
        return continuity, discovery, cache, aggregates

    def test_backoff_member_becomes_hold_only_plan(self) -> None:
        clock = _Clock()
        continuity, discovery, _cache, aggregates = self._continuity(clock)
        discovery.pv_candidates.return_value = [_MEMBER]
        discovery.pv_members.return_value = []
        state = AggregateState(("pv-total", (_MEMBER,)), 0.2)
        self.assertEqual(continuity.record_confirmed(state, *_MEMBER, 1000.0), 1000.0)
        continuity.record_error(state, *_MEMBER, TimeoutError("timeout"))

        members, held = continuity.plan("pv_power_w", {"optional_confidence": 0.2})

        self.assertEqual(members, ())
        self.assertIsNotNone(held)
        assert held is not None
        self.assertEqual(held.total, 800.0)
        self.assertTrue(held.estimated)
        self.assertEqual(held.sources, [f"{_SERVICE}{_PATH}"])
        self.assertTrue(aggregates.has_pending())

    def test_in_progress_members_are_reused_without_rediscovery(self) -> None:
        clock = _Clock()
        continuity, discovery, _cache, aggregates = self._continuity(clock)
        cached = (_SERVICE, "/Cached")
        aggregates.state_for(
            "pv_power_w",
            ("pv-total", (cached,)),
            0.2,
        )
        discovery.pv_candidates.return_value = [cached]

        members, held = continuity.plan("pv_power_w", {})

        self.assertEqual(members, (cached,))
        self.assertIsNone(held)
        discovery.pv_members.assert_not_called()

    def test_empty_plan_discards_aggregate_and_candidate_bookkeeping(self) -> None:
        clock = _Clock()
        continuity, discovery, cache, aggregates = self._continuity(clock)
        discovery.pv_candidates.return_value = [_MEMBER]
        discovery.pv_members.return_value = []
        state = AggregateState(("pv-total", (_MEMBER,)), 0.2)
        continuity.record_confirmed(state, *_MEMBER, 1000.0)
        continuity.record_error(state, *_MEMBER, TimeoutError())
        cache.services = {}

        with (
            patch.object(aggregates, "state_for", wraps=aggregates.state_for) as state_for,
            patch.object(continuity, "discard", wraps=continuity.discard) as discard,
        ):
            members, held = continuity.plan(
                "pv_power_w",
                {"optional_confidence": 0.37},
            )

        self.assertEqual((members, held), ((), None))
        state_for.assert_called_once_with(
            "pv_power_w",
            ("pv-total", ()),
            0.37,
        )
        self.assertFalse(aggregates.has_pending())
        discard.assert_called_once_with("pv_power_w")

        cache.services = {_SERVICE: {}}
        probe = AggregateState(("pv-total", (_MEMBER,)), 0.2)
        continuity.add_estimates("pv_power_w", probe)
        self.assertFalse(probe.estimated)
        self.assertEqual(continuity._available_estimates("missing"), ())

    def test_probe_plan_and_non_pv_values_remain_unchanged(self) -> None:
        clock = _Clock()
        continuity, discovery, _cache, _aggregates = self._continuity(clock)
        discovery.pv_candidates.return_value = [_MEMBER]
        discovery.pv_members.return_value = [_MEMBER]
        members, held = continuity.plan("pv_power_w", {})
        self.assertEqual(members, (_MEMBER,))
        self.assertIsNone(held)

        state = AggregateState(("sum", (_MEMBER,)), 1.0)
        marker = object()
        self.assertIs(continuity.record_confirmed(state, *_MEMBER, marker), marker)
        continuity.record_error(state, *_MEMBER, TimeoutError())
        continuity.add_estimates("pv_power_w", state)
        self.assertFalse(state.estimated)

    def test_service_loss_invalidates_hold_before_aggregate_completion(self) -> None:
        clock = _Clock()
        continuity, discovery, cache, aggregates = self._continuity(clock)
        discovery.pv_candidates.return_value = [_MEMBER]
        discovery.pv_members.return_value = [_MEMBER]
        state = aggregates.state_for("pv_power_w", ("pv-total", (_MEMBER,)), 0.2)
        continuity.plan("pv_power_w", {})
        continuity.record_confirmed(state, *_MEMBER, 1000.0)
        continuity.record_error(state, *_MEMBER, TimeoutError())
        cache.services = {}

        continuity.add_estimates("pv_power_w", state)

        self.assertFalse(state.estimated)
        continuity.discard("pv_power_w")
        continuity.discard("pv_power_w")

    def test_discard_prevents_a_finished_cycle_from_reusing_candidates(self) -> None:
        clock = _Clock()
        continuity, discovery, _cache, _aggregates = self._continuity(clock)
        discovery.pv_candidates.return_value = [_MEMBER]
        discovery.pv_members.return_value = [_MEMBER]
        state = AggregateState(("pv-total", (_MEMBER,)), 0.2)
        continuity.plan("pv_power_w", {})
        continuity.record_confirmed(state, *_MEMBER, 1000.0)
        continuity.record_error(state, *_MEMBER, TimeoutError())

        continuity.discard("pv_power_w")
        probe = AggregateState(("pv-total", (_MEMBER,)), 0.2)
        continuity.add_estimates("pv_power_w", probe)

        self.assertFalse(probe.estimated)

    def test_complete_publishes_exact_confirmed_metadata_and_clears_cycle(self) -> None:
        clock = _Clock()
        continuity, discovery, cache, aggregates = self._continuity(clock)
        discovery.pv_candidates.return_value = [_MEMBER]
        discovery.pv_members.return_value = [_MEMBER]
        continuity.plan("pv_power_w", {})
        sample_state = AggregateState(("pv-total", (_MEMBER,)), 0.2)
        continuity.record_confirmed(sample_state, *_MEMBER, 1000.0)
        continuity.record_error(sample_state, *_MEMBER, TimeoutError())
        cache.services = {}
        state = aggregates.state_for("pv_power_w", ("pv-total", ()), 0.37)

        with patch.object(continuity, "discard", wraps=continuity.discard) as discard:
            continuity.complete("pv_power_w", state, stale_after_seconds=7.5)

        cache.update_external_read.assert_called_once_with(
            "pv_power_w",
            0.0,
            source="pv_power_w",
            confidence=0.37,
            last_error="",
            stale_after_seconds=7.5,
        )
        self.assertFalse(aggregates.has_pending())
        discard.assert_called_once_with("pv_power_w")
        cache.services = {_SERVICE: {}}
        probe = AggregateState(("pv-total", (_MEMBER,)), 0.2)
        continuity.add_estimates("pv_power_w", probe)
        self.assertFalse(probe.estimated)

    def test_complete_publishes_exact_estimate_metadata(self) -> None:
        clock = _Clock()
        continuity, _discovery, cache, aggregates = self._continuity(clock)
        state = aggregates.state_for(
            "pv_power_w",
            ("pv-total", (_MEMBER,)),
            0.2,
        )
        state.record_estimate(*_MEMBER, 400.0, confidence=0.4)
        state.record_error(*_MEMBER, TimeoutError("timed out"))

        continuity.complete("pv_power_w", state, stale_after_seconds=7.5)

        cache.update_external_read.assert_called_once_with(
            "pv_power_w",
            400.0,
            source=f"{_SERVICE}{_PATH}",
            confidence=0.4,
            last_error=f"{_SERVICE}{_PATH}: timed out",
            stale_after_seconds=7.5,
            status="stale",
            confirmed=False,
            reason_code=PV_TRANSIENT_HOLD_REASON,
        )
        self.assertFalse(aggregates.has_pending())


class PvLastGoodGatewayIntegrationTests(unittest.TestCase):
    def test_gateway_publishes_decaying_stale_estimate_without_reconfirming_it(self) -> None:
        clock = _Clock()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            adapter.read_executor = DbusReadExecutor(adapter, monotonic=clock.monotonic)
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services([_SERVICE])
            adapter.energy_discovery.update_services([_SERVICE], captured_at=1.0)
            response: dict[str, object] = {"value": 1000.0}

            def read(_service: str, _path: str) -> object:
                value = response["value"]
                if isinstance(value, BaseException):
                    raise value
                return value

            read_call = install_read_responder(adapter, read)
            spec = {
                "aggregate": "pv-total",
                "prefix": "com.victronenergy.pvinverter.",
                "path": _PATH,
                "use_dc_pv": False,
                "optional_zero_on_error": True,
                "optional_confidence": 0.2,
                "interval": 1.0,
            }
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv_power_w", spec),
                "applied",
            )
            confirmed_at = adapter.cache.values["pv_power_w"]["confirmed_at"]
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 1000.0)

            clock.value = 101.0
            response["value"] = TimeoutError("timed out")
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv_power_w", spec),
                "applied",
            )
            held = adapter.cache.values["pv_power_w"]
            self.assertEqual(held["value"], 800.0)
            self.assertEqual(held["status"], "stale")
            self.assertEqual(held["reason_code"], PV_TRANSIENT_HOLD_REASON)
            self.assertEqual(held["confidence"], 0.8)
            self.assertEqual(held["confirmed_at"], confirmed_at)
            self.assertEqual(read_call.call_count, 2)

            clock.value = 103.5
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv_power_w", spec),
                "applied",
            )
            decayed = adapter.cache.values["pv_power_w"]
            self.assertAlmostEqual(float(decayed["value"]), 400.0)
            self.assertAlmostEqual(float(decayed["confidence"]), 0.4)
            self.assertEqual(decayed["confirmed_at"], confirmed_at)
            self.assertEqual(read_call.call_count, 2)

            snapshot = energy_inputs_snapshot(
                adapter.cache.values,
                adapter.energy_discovery,
                sequence=adapter.cache.sequence,
                captured_at=float(decayed["updated_at"]),
                captured_monotonic=float(decayed["updated_monotonic"]),
            )
            self.assertEqual(snapshot.pv_power_w.status, "stale")
            self.assertEqual(snapshot.pv_power_w.reason_code, PV_TRANSIENT_HOLD_REASON)

            clock.value = 106.0
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv_power_w", spec),
                "applied",
            )
            expired = adapter.cache.values["pv_power_w"]
            self.assertEqual(expired["value"], 0.0)
            self.assertEqual(expired["confidence"], 0.2)
            self.assertEqual(expired.get("reason_code", ""), "")


if __name__ == "__main__":
    unittest.main()
