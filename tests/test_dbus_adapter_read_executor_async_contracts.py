#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact callback and lifecycle contracts for asynchronous DBus reads."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.dbus_adapter.read.aggregate import (
    AggregateState,
    AggregateStepContinuation,
    AggregateStepPlan,
)
from venus_evcharger.dbus_adapter.read.executor import DbusReadExecutor
from venus_evcharger.dbus_adapter.read.pv import PV_MEMBER_ERROR_BACKOFF_SECONDS
from venus_evcharger.dbus_adapter.read.transport import BusItemReadCall
from venus_evcharger.dbus_gateway import dbus_path_key


class DbusReadExecutorAsyncContractTests(unittest.TestCase):
    """Pin the asynchronous read edge independently of immediate test brokers."""

    @staticmethod
    def _executor() -> tuple[DbusReadExecutor, MagicMock]:
        adapter = MagicMock()
        return DbusReadExecutor(adapter), adapter

    def test_poll_resets_operation_state_and_keeps_deferred_stale_horizon(self) -> None:
        executor, _adapter = self._executor()
        executor.last_operation_performed = True
        completion = MagicMock()

        def defer(
            key: str,
            spec: object,
            completed: list[str],
            callback: object,
        ) -> None:
            self.assertEqual(key, "power")
            self.assertEqual(spec, {"interval": 2.0})
            self.assertIsInstance(completed, list)
            self.assertEqual(completed, [])
            self.assertTrue(callable(callback))
            callback("deferred")

        with patch.object(executor, "_poll_read_with_recovery", side_effect=defer):
            outcome = executor.poll_read_spec(
                "power",
                {"interval": 2.0},
                completion=completion,
            )

        self.assertIs(executor.last_operation_performed, False)
        self.assertEqual(outcome, "deferred")
        self.assertEqual(executor._stale_after_by_key, {"power": 6.0})
        completion.assert_called_once_with("deferred")

    def test_recovery_does_not_duplicate_or_invent_callback_completion(self) -> None:
        executor, _adapter = self._executor()
        spec = {"service": "svc", "path": "/Power"}
        completed: list[str] = []

        def complete_and_return(
            _key: str,
            _spec: object,
            completion: object,
        ) -> str:
            self.assertTrue(callable(completion))
            completion("applied")
            return "applied"

        with patch.object(executor, "_poll_read_spec_unchecked", side_effect=complete_and_return):
            executor._poll_read_with_recovery("power", spec, completed, completed.append)
        self.assertEqual(completed, ["applied"])

        completion = MagicMock()
        with patch.object(executor, "_poll_read_spec_unchecked", return_value="deferred"):
            executor._poll_read_with_recovery("power", spec, [], completion)
        completion.assert_not_called()

        with patch.object(
            executor,
            "_poll_read_spec_unchecked",
            side_effect=DbusOperationDeferred("busy"),
        ):
            executor._poll_read_with_recovery("power", spec, [], completion)
        completion.assert_not_called()

    def test_non_pv_dispatch_clears_only_its_interval_factor(self) -> None:
        executor, _adapter = self._executor()
        completion = MagicMock()
        executor._interval_factors = {"pv": 4.0, "other": 5.0}

        with patch.object(executor, "_poll_pv_total_step", return_value="deferred") as pv_handler:
            self.assertEqual(
                executor._poll_read_spec_unchecked(
                    "pv",
                    {"aggregate": "pv-total"},
                    completion,
                ),
                "deferred",
            )
        self.assertEqual(executor._interval_factors, {"pv": 4.0, "other": 5.0})
        pv_handler.assert_called_once_with("pv", {"aggregate": "pv-total"}, completion)

        with patch.object(executor, "_poll_direct_spec", return_value="deferred") as direct_handler:
            self.assertEqual(
                executor._poll_read_spec_unchecked("pv", {}, completion),
                "deferred",
            )
        self.assertEqual(executor._interval_factors, {"other": 5.0})
        direct_handler.assert_called_once_with("pv", {}, completion)

    def test_direct_schedule_reports_pending_until_callback(self) -> None:
        executor, _adapter = self._executor()
        completion = MagicMock()
        submit = MagicMock()

        with patch.object(executor, "_submit_busitem", submit):
            outcome = executor._poll_direct_spec(
                "power",
                {"service": "svc.read", "path": "/Power"},
                completion,
            )

        self.assertEqual(outcome, "deferred")
        self.assertEqual(submit.call_args.args, ("svc.read", "/Power"))
        self.assertIs(callable(submit.call_args.kwargs["on_success"]), True)
        self.assertIs(callable(submit.call_args.kwargs["on_error"]), True)

    def test_read_failures_clear_only_the_matching_aggregate_metadata(self) -> None:
        spec = {"service": "svc.read", "path": "/Power", "optional_zero_on_error": True}
        error = RuntimeError("offline")
        for method_name in ("_mark_read_error", "_mark_optional_zero"):
            with self.subTest(method=method_name):
                executor, _adapter = self._executor()
                executor._aggregates.state_for("power", ("sum", ()), 1.0)
                executor._stale_after_by_key = {"power": 6.0, "other": 7.0}
                executor._interval_factors = {"power": 4.0, "other": 5.0}

                getattr(executor, method_name)("power", spec, error)

                self.assertFalse(executor.has_pending_aggregate())
                self.assertEqual(executor._stale_after_by_key, {"other": 7.0})
                self.assertEqual(executor._interval_factors, {"other": 5.0})

    def test_first_service_error_and_async_callbacks_preserve_exact_context(self) -> None:
        executor, adapter = self._executor()
        spec = {"aggregate": "first-service", "prefix": "svc.prefix", "path": "/Power"}
        completion = MagicMock()
        adapter.energy_discovery.first_service.return_value = None
        with self.assertRaisesRegex(
            RuntimeError,
            "^No cached services for prefix 'svc\\.prefix'$",
        ):
            executor._poll_first_service("power", spec, completion)

        adapter.energy_discovery.first_service.return_value = "svc.read"
        submit = MagicMock()
        read_error = MagicMock(return_value="dropped")
        with (
            patch.object(executor, "_submit_busitem", submit),
            patch.object(executor, "_read_error_outcome", read_error),
        ):
            outcome = executor._poll_first_service("power", spec, completion)
            error = RuntimeError("offline")
            submit.call_args.kwargs["on_error"](error)

        self.assertEqual(outcome, "deferred")
        self.assertEqual(submit.call_args.args, ("svc.read", "/Power"))
        read_error.assert_called_once_with("power", spec, error)
        completion.assert_called_once_with("dropped")

    def test_aggregate_step_resets_factor_only_at_first_member(self) -> None:
        executor, _adapter = self._executor()
        completion = MagicMock()
        plan = AggregateStepPlan(
            key="pv",
            signature=("pv-total", (("svc.one", "/Power"), ("svc.two", "/Power"))),
            members=(("svc.one", "/Power"), ("svc.two", "/Power")),
            completion=completion,
            ignore_member_errors=True,
            empty_confidence=0.2,
        )
        executor._interval_factors["pv"] = 9.0
        submit = MagicMock()

        with patch.object(executor, "_submit_busitem", submit):
            self.assertEqual(executor._poll_aggregate_step(plan), "deferred")
            self.assertEqual(executor._interval_factors["pv"], 1.0)
            self.assertEqual(submit.call_args.args, ("svc.one", "/Power"))
            self.assertIs(submit.call_args.kwargs["optional"], True)

            state = executor._aggregates.state_for("pv", plan.signature, 0.2)
            state.index = 1
            executor._interval_factors["pv"] = 4.0
            submit.reset_mock()
            self.assertEqual(executor._poll_aggregate_step(plan), "deferred")

        self.assertEqual(executor._interval_factors["pv"], 4.0)
        self.assertEqual(submit.call_args.args, ("svc.two", "/Power"))
        self.assertIs(submit.call_args.kwargs["optional"], True)

    def test_successful_aggregate_member_records_exact_discovery_identity(self) -> None:
        executor, adapter = self._executor()
        state = AggregateState(("sum", ()), empty_confidence=1.0)
        completion = MagicMock()
        continuation = AggregateStepContinuation(
            key="power",
            state=state,
            service="svc.read",
            path="/Power",
            member_count=2,
            ignore_member_errors=False,
            completion=completion,
        )

        executor._complete_aggregate_member(continuation, 3.5)

        adapter.energy_discovery.record_pv_value.assert_called_once_with(
            "svc.read",
            "/Power",
            3.5,
        )
        self.assertEqual(state.index, 1)
        completion.assert_called_once_with("deferred")

    def test_official_pv_aggregate_members_do_not_duplicate_discovery_records(self) -> None:
        executor, adapter = self._executor()
        adapter.circuit.optional_source_interval_factor.return_value = 1.0
        state = AggregateState(("pv-total", ()), empty_confidence=0.2)
        success_completion = MagicMock()
        success = AggregateStepContinuation(
            key="pv_power_w",
            state=state,
            service="com.victronenergy.system",
            path="/Ac/PvOnGrid/Total/Power",
            member_count=2,
            ignore_member_errors=True,
            completion=success_completion,
            record_discovery_values=False,
        )

        executor._complete_aggregate_member(success, 1250.0)

        adapter.energy_discovery.record_pv_value.assert_not_called()
        success_completion.assert_called_once_with("deferred")

        error_completion = MagicMock()
        error = AggregateStepContinuation(
            key="pv_power_w",
            state=state,
            service="com.victronenergy.system",
            path="/Dc/Pv/Power",
            member_count=2,
            ignore_member_errors=True,
            completion=error_completion,
            record_discovery_values=False,
        )
        failure = RuntimeError("temporarily unavailable")

        executor._complete_aggregate_error(error, failure)

        adapter.energy_discovery.record_pv_error.assert_not_called()
        self.assertEqual(state.index, 2)
        error_completion.assert_called_once_with("applied")

    def test_official_pv_aggregate_replaces_member_fanout_but_keeps_dc_input(self) -> None:
        executor, _adapter = self._executor()
        completion = MagicMock()
        with patch.object(
            executor,
            "_poll_aggregate_step",
            MagicMock(return_value="deferred"),
        ) as poll:
            self.assertEqual(
                executor._poll_pv_total_step(
                    "pv_power_w",
                    {
                        "aggregate": "pv-total",
                        "aggregate_service": "com.victronenergy.system",
                        "aggregate_paths": ["/Ac/PvOnGrid/Total/Power"],
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": True,
                    },
                    completion,
                ),
                "deferred",
            )

        plan = poll.call_args.args[0]
        self.assertEqual(
            plan.members,
            (
                ("com.victronenergy.system", "/Ac/PvOnGrid/Total/Power"),
                ("com.victronenergy.system", "/Dc/Pv/Power"),
            ),
        )
        self.assertFalse(plan.record_discovery_values)
        self.assertIs(plan.completion, completion)

    def test_aggregate_error_paths_preserve_member_and_key_identity(self) -> None:
        executor, _adapter = self._executor()
        error = RuntimeError("offline")
        strict_completion = MagicMock()
        strict = AggregateStepContinuation(
            key="power",
            state=AggregateState(("sum", ()), empty_confidence=1.0),
            service="svc.read",
            path="/Power",
            member_count=1,
            ignore_member_errors=False,
            completion=strict_completion,
        )
        with patch.object(executor, "_mark_read_error") as mark_error:
            executor._complete_aggregate_error(strict, error)
        mark_error.assert_called_once_with(
            "power",
            {"service": "svc.read", "path": "/Power"},
            error,
        )
        strict_completion.assert_called_once_with("dropped")

        optional_completion = MagicMock()
        optional = AggregateStepContinuation(
            key="pv",
            state=AggregateState(("pv-total", ()), empty_confidence=0.2),
            service="svc.pv",
            path="/Ac/Power",
            member_count=2,
            ignore_member_errors=True,
            completion=optional_completion,
        )
        with (
            patch.object(executor, "_record_optional_aggregate_error") as record_error,
            patch.object(executor, "_record_optional_interval_factor") as record_factor,
            patch.object(executor, "_record_aggregate_member") as record_member,
        ):
            executor._complete_aggregate_error(optional, error)

        record_error.assert_called_once_with("svc.pv", "/Ac/Power", optional.state, error)
        record_factor.assert_called_once_with("pv", "svc.pv", "/Ac/Power")
        record_member.assert_called_once_with(optional.state, "svc.pv", "/Ac/Power", None)
        self.assertEqual(optional.state.index, 1)
        optional_completion.assert_called_once_with("deferred")

    def test_optional_member_error_records_discovery_cache_and_state(self) -> None:
        executor, adapter = self._executor()
        state = AggregateState(("pv-total", ()), empty_confidence=0.2)
        error = RuntimeError("asleep")

        executor._record_optional_aggregate_error(
            "svc.pv",
            "/Ac/Power",
            state,
            error,
        )

        adapter.energy_discovery.record_pv_error.assert_called_once_with(
            "svc.pv",
            "/Ac/Power",
            error,
        )
        adapter.cache.mark_unavailable.assert_called_once_with(
            dbus_path_key("svc.pv", "/Ac/Power"),
            source="svc.pv/Ac/Power",
            error=error,
            retry_after_seconds=PV_MEMBER_ERROR_BACKOFF_SECONDS,
        )
        self.assertEqual(state.errors, ["svc.pv/Ac/Power: asleep"])

    def test_optional_interval_factor_accumulates_per_read_key(self) -> None:
        executor, adapter = self._executor()
        executor._interval_factors["pv"] = 4.0
        adapter.circuit.optional_source_interval_factor.side_effect = (2.0, 1.25)

        executor._record_optional_interval_factor("pv", "svc.one", "/Power")
        executor._record_optional_interval_factor("grid", "svc.two", "/Power")

        self.assertEqual(executor._interval_factors, {"pv": 4.0, "grid": 1.25})
        self.assertEqual(
            adapter.circuit.optional_source_interval_factor.call_args_list,
            [call("svc.one/Power"), call("svc.two/Power")],
        )

    def test_submit_busitem_preserves_transport_contract_and_operation_flag(self) -> None:
        executor, adapter = self._executor()
        on_success = MagicMock()
        on_error = MagicMock()

        with patch(
            "venus_evcharger.dbus_adapter.read.executor.submit_busitem_read"
        ) as submit:
            executor._submit_busitem(
                "svc.read",
                "/Power",
                on_success=on_success,
                on_error=on_error,
                optional=True,
            )

        submit.assert_called_once_with(
            adapter,
            BusItemReadCall("svc.read", "/Power", True),
            on_success=on_success,
            on_error=on_error,
        )
        self.assertIs(executor.last_operation_performed, True)


if __name__ == "__main__":
    unittest.main()
