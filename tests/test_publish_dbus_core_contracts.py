# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH
from venus_evcharger.publish.dbus_core import _DbusPublishCore


class _FailingDbusService(dict[str, object]):
    def __setitem__(self, key: str, value: object) -> None:
        if key == "/Fail":
            raise RuntimeError("write failed")
        super().__setitem__(key, value)


class _DbusCoreHarness(_DbusPublishCore):
    PHASE_NAMES = ("L1", "L2", "L3")

    def __init__(self, service: object) -> None:
        self.service = service


def _service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "_dbusservice": {},
        "_dbus_publish_state": {},
        "_dbus_live_publish_interval_seconds": 1.0,
        "_dbus_slow_publish_interval_seconds": 5.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DbusPublishCoreContractTests(unittest.TestCase):
    def test_ensure_state_initializes_defaults_and_preserves_existing_values(self) -> None:
        service = SimpleNamespace()
        _DbusCoreHarness(service).ensure_state()

        self.assertEqual(service._dbus_publish_state, {})
        self.assertEqual(service._dbus_live_publish_interval_seconds, 1.0)
        self.assertEqual(service._dbus_slow_publish_interval_seconds, 5.0)

        existing = _service(
            _dbus_publish_state={"/A": {"value": 1}},
            _dbus_live_publish_interval_seconds=2.5,
            _dbus_slow_publish_interval_seconds=9.5,
        )
        _DbusCoreHarness(existing).ensure_state()

        self.assertEqual(existing._dbus_publish_state, {"/A": {"value": 1}})
        self.assertEqual(existing._dbus_live_publish_interval_seconds, 2.5)
        self.assertEqual(existing._dbus_slow_publish_interval_seconds, 9.5)

    def test_should_enqueue_publish_requires_mainloop_queue_and_disallows_direct_mode(self) -> None:
        self.assertFalse(_DbusCoreHarness(_service(_dbus_publish_direct_allowed=None))._should_enqueue_publish())
        self.assertFalse(
            _DbusCoreHarness(
                _service(
                    _dbus_publish_direct_allowed=MagicMock(return_value=True),
                    _enqueue_dbus_publish_values=MagicMock(),
                )
            )._should_enqueue_publish()
        )
        self.assertFalse(
            _DbusCoreHarness(
                _service(_dbus_publish_direct_allowed=MagicMock(return_value=False))
            )._should_enqueue_publish()
        )
        self.assertTrue(
            _DbusCoreHarness(
                _service(
                    _dbus_publish_direct_allowed=MagicMock(return_value=False),
                    _enqueue_dbus_publish_values=MagicMock(),
                )
            )._should_enqueue_publish()
        )
        self.assertTrue(
            _DbusCoreHarness(
                _service(
                    _dbus_publish_direct_allowed=MagicMock(return_value=False),
                    _enqueue_dbus_publish_fields=MagicMock(),
                )
            )._should_enqueue_publish()
        )

    def test_effective_publish_interval_uses_backpressure_unless_forced_or_unthrottled(self) -> None:
        harness = _DbusCoreHarness(_service())
        policy = MagicMock()
        policy.publish_interval_seconds.return_value = 7.5

        with patch("venus_evcharger.publish.dbus_core.service_dbus_backpressure_policy", return_value=policy) as policy_factory:
            self.assertEqual(harness._effective_publish_interval(2.0, group_name="live", force=False), 7.5)
            self.assertEqual(harness._effective_publish_interval(2.0, group_name="live", force=True), 2.0)
            self.assertIsNone(harness._effective_publish_interval(None, group_name="live", force=False))

        policy_factory.assert_called_once_with(harness.service)
        policy.publish_interval_seconds.assert_called_once_with(2.0, group="live")

    def test_publish_interval_elapsed_treats_missing_timestamp_as_due(self) -> None:
        self.assertTrue(_DbusCoreHarness._publish_interval_elapsed(None, 10.0, 5.0))
        self.assertFalse(_DbusCoreHarness._publish_interval_elapsed(8.0, 10.0, 5.0))
        self.assertTrue(_DbusCoreHarness._publish_interval_elapsed(5.0, 10.0, 5.0))

    def test_publish_path_throttles_changes_and_force_republishes_same_value(self) -> None:
        service = _service(_assert_dbus_mainloop_thread=MagicMock())
        harness = _DbusCoreHarness(service)

        self.assertTrue(harness.publish_path("/Ac/Power", 12.0, now=10.0, interval_seconds=5.0))
        self.assertFalse(harness.publish_path("/Ac/Power", 12.0, now=11.0, interval_seconds=5.0))
        self.assertTrue(harness.publish_path("/Ac/Power", 12.0, now=11.5, interval_seconds=5.0, force=True))
        self.assertEqual(service._dbusservice["/Ac/Power"], 12.0)
        self.assertEqual(service._dbus_publish_state["/Ac/Power"], {"value": 12.0, "updated_at": 11.5})
        service._assert_dbus_mainloop_thread.assert_any_call("publish /Ac/Power")

    def test_publish_path_delegates_policy_decision_and_enqueue_contract(self) -> None:
        service = _service(
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=MagicMock(return_value=True),
        )
        harness = _DbusCoreHarness(service)
        with patch.object(harness, "_effective_publish_interval", return_value=9.0) as interval, patch.object(
            harness, "_publish_decision", return_value=(True, None)
        ) as decision:
            self.assertTrue(harness.publish_path("/Mode", 2, now=12.0, interval_seconds=3.0, force=False))

        interval.assert_called_once_with(3.0, group_name="single-path", force=False)
        decision.assert_called_once_with("/Mode", 2, 12.0, 9.0, False)
        service._enqueue_dbus_publish_values.assert_called_once_with([("/Mode", 2)], 12.0)

    def test_publish_field_and_publish_values_preserve_group_contracts(self) -> None:
        service = _service()
        harness = _DbusCoreHarness(service)

        with patch.object(harness, "_publish_fields_transactional", return_value=True) as publish_fields:
            self.assertTrue(harness.publish_field("mode", 2, now=15.0, interval_seconds=3.0, force=True))
        publish_fields.assert_called_once_with(
            "single-field",
            {"mode": 2},
            15.0,
            interval_seconds=3.0,
            force=True,
        )
        with patch.object(harness, "_publish_fields_transactional", return_value=True) as publish_fields:
            self.assertTrue(harness.publish_field("mode", 2, now=15.5))
        publish_fields.assert_called_once_with(
            "single-field",
            {"mode": 2},
            15.5,
            interval_seconds=None,
            force=False,
        )

        with patch.object(harness, "_publish_values_transactional", return_value=True) as publish_values:
            self.assertTrue(harness._publish_values({"/Mode": 2}, now=16.0, interval_seconds=4.0, force=True))
        publish_values.assert_called_once_with(
            "generic",
            {"/Mode": 2},
            16.0,
            interval_seconds=4.0,
            force=True,
        )
        with patch.object(harness, "_publish_values_transactional", return_value=True) as publish_values:
            self.assertTrue(harness._publish_values({"/Mode": 2}, now=16.5))
        publish_values.assert_called_once_with(
            "generic",
            {"/Mode": 2},
            16.5,
            interval_seconds=None,
            force=False,
        )

    def test_field_publish_enqueues_semantic_fields_or_falls_back_to_paths_with_timestamp(self) -> None:
        fields_queue = MagicMock(return_value=True)
        field_service = _service(
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_fields=fields_queue,
        )
        self.assertTrue(_DbusCoreHarness(field_service).publish_field("ac_power_w", 50.0, now=21.0, force=True))
        fields_queue.assert_called_once_with([("ac_power_w", 50.0)], 21.0)

        path_queue = MagicMock(return_value=True)
        path_service = _service(
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=path_queue,
        )
        self.assertTrue(_DbusCoreHarness(path_service).publish_field("ac_power_w", 51.0, now=22.0, force=True))
        path_queue.assert_called_once_with([(EVCS_FIELD_TO_PATH["ac_power_w"], 51.0)], 22.0)

    def test_transactional_publish_success_delegates_policy_staging_and_apply_steps(self) -> None:
        harness = _DbusCoreHarness(_service())
        with patch.object(harness, "_effective_publish_interval", return_value=4.0) as interval, patch.object(
            harness,
            "_stage_publish_values",
            return_value=([("/A", 1)], {"/A": None}, {"/A": (False, None)}),
        ) as stage, patch.object(harness, "_apply_staged_publish_values", return_value=(True, ["/A"], None)) as apply:
            self.assertTrue(
                harness._publish_values_transactional(
                    "group-a",
                    {"/A": 1},
                    now=30.0,
                    interval_seconds=2.0,
                    force=True,
                )
            )

        interval.assert_called_once_with(2.0, group_name="group-a", force=True)
        stage.assert_called_once_with({"/A": 1}, 30.0, 4.0, True)
        apply.assert_called_once_with([("/A", 1)], 30.0)

    def test_transactional_publish_default_force_does_not_republish_unchanged_throttled_values(self) -> None:
        service = _service(_dbus_publish_state={"/A": {"value": 1, "updated_at": 10.0}})
        harness = _DbusCoreHarness(service)

        self.assertFalse(harness._publish_values_transactional("default-force", {"/A": 1}, now=11.0, interval_seconds=5.0))
        self.assertEqual(service._dbusservice, {})

    def test_transactional_publish_enqueue_branch_preserves_values_current_interval_and_force(self) -> None:
        harness = _DbusCoreHarness(_service())
        with patch.object(harness, "_should_enqueue_publish", return_value=True), patch.object(
            harness, "_effective_publish_interval", return_value=8.0
        ) as interval, patch.object(harness, "_enqueue_transactional_publish", return_value=True) as enqueue:
            self.assertTrue(
                harness._publish_values_transactional(
                    "queued",
                    {"/A": 1},
                    now=32.0,
                    interval_seconds=6.0,
                    force=True,
                )
            )

        interval.assert_called_once_with(6.0, group_name="queued", force=True)
        enqueue.assert_called_once_with({"/A": 1}, 32.0, 8.0, True)

    def test_enqueue_transactional_publish_queues_only_due_values(self) -> None:
        service = _service(_enqueue_dbus_publish_values=MagicMock(return_value=True))
        harness = _DbusCoreHarness(service)
        with patch.object(harness, "_staged_values_for_enqueue", return_value=[("/A", 1)]) as staged:
            self.assertTrue(harness._enqueue_transactional_publish({"/A": 1}, 30.0, 4.0, False))
        staged.assert_called_once_with({"/A": 1}, 30.0, 4.0, False)
        service._enqueue_dbus_publish_values.assert_called_once_with([("/A", 1)], 30.0)

        with patch.object(harness, "_staged_values_for_enqueue", return_value=[]) as staged:
            self.assertFalse(harness._enqueue_transactional_publish({"/A": 1}, 31.0, 4.0, False))
        staged.assert_called_once_with({"/A": 1}, 31.0, 4.0, False)

    def test_transactional_publish_failure_restores_and_reports_failed_path(self) -> None:
        harness = _DbusCoreHarness(_service())
        staged_entries = {"/A": {"value": "old", "updated_at": 1.0}}
        original_values = {"/A": (True, "old")}
        with patch.object(
            harness,
            "_stage_publish_values",
            return_value=([("/A", "new"), ("/Fail", "bad")], staged_entries, original_values),
        ), patch.object(
            harness,
            "_apply_staged_publish_values",
            return_value=(True, ["/A"], "/Fail"),
        ), patch.object(harness, "_restore_service_values") as restore_values, patch.object(
            harness, "_restore_group_publish_state"
        ) as restore_state, patch.object(harness, "_publish_group_failure") as publish_failure:
            self.assertFalse(harness._publish_values_transactional("group-b", {"/A": "new"}, now=31.0, force=False))

        restore_values.assert_called_once_with(["/A"], original_values)
        restore_state.assert_called_once_with(staged_entries)
        publish_failure.assert_called_once_with("group-b", ["/Fail"], 31.0)

    def test_publish_fields_transactional_uses_semantic_paths_for_direct_publish(self) -> None:
        harness = _DbusCoreHarness(_service())
        with patch.object(harness, "_publish_values_transactional", return_value=True) as publish_values:
            self.assertTrue(
                harness._publish_fields_transactional(
                    "fields",
                    {"mode": 1, "ac_power_w": 50.0, "unknown": "ignored"},
                    now=33.0,
                    interval_seconds=7.0,
                    force=True,
                )
            )

        publish_values.assert_called_once_with(
            "fields",
            {
                EVCS_FIELD_TO_PATH["mode"]: 1,
                EVCS_FIELD_TO_PATH["ac_power_w"]: 50.0,
            },
            33.0,
            interval_seconds=7.0,
            force=True,
        )

    def test_publish_fields_transactional_enqueue_branch_preserves_effective_interval(self) -> None:
        harness = _DbusCoreHarness(_service())
        paths = {EVCS_FIELD_TO_PATH["mode"]: 2}
        with patch.object(harness, "_should_enqueue_publish", return_value=True), patch.object(
            harness, "_effective_publish_interval", return_value=9.0
        ) as interval, patch(
            "venus_evcharger.publish.dbus_core.evcs_fields_to_paths",
            return_value=paths,
        ) as translate, patch.object(
            harness, "_staged_fields_for_enqueue", return_value=[("mode", 2)]
        ) as staged, patch.object(harness, "_enqueue_publish_fields", return_value=True) as enqueue:
            self.assertTrue(
                harness._publish_fields_transactional(
                    "queued-fields",
                    {"mode": 2},
                    now=34.0,
                    interval_seconds=5.0,
                    force=False,
                )
            )

        translate.assert_called_once_with({"mode": 2})
        interval.assert_called_once_with(5.0, group_name="queued-fields", force=False)
        staged.assert_called_once_with({"mode": 2}, paths, 34.0, 9.0, False)
        enqueue.assert_called_once_with([("mode", 2)], 34.0)

    def test_stage_publish_values_collects_only_due_paths_with_original_snapshots(self) -> None:
        harness = _DbusCoreHarness(_service())
        with patch.object(
            harness,
            "_publish_decision",
            side_effect=[(False, {"value": 1}), (True, {"value": 2}), (True, None)],
        ) as decision, patch.object(
            harness,
            "_service_value_snapshot",
            side_effect=[(True, "old-b"), (False, None)],
        ) as snapshot:
            staged, entries, originals = harness._stage_publish_values(
                {"/A": 1, "/B": 2, "/C": 3},
                44.0,
                5.0,
                False,
            )

        self.assertEqual(staged, [("/B", 2), ("/C", 3)])
        self.assertEqual(entries, {"/B": {"value": 2}, "/C": None})
        self.assertEqual(originals, {"/B": (True, "old-b"), "/C": (False, None)})
        self.assertEqual(decision.call_count, 3)
        decision.assert_any_call("/A", 1, 44.0, 5.0, False)
        decision.assert_any_call("/B", 2, 44.0, 5.0, False)
        decision.assert_any_call("/C", 3, 44.0, 5.0, False)
        snapshot.assert_any_call("/B")
        snapshot.assert_any_call("/C")

    def test_staged_value_and_field_helpers_preserve_due_order_and_unknown_field_filtering(self) -> None:
        service = _service(
            _dbus_publish_state={
                "/A": {"value": 1, "updated_at": 10.0},
                EVCS_FIELD_TO_PATH["mode"]: {"value": 1, "updated_at": 10.0},
            }
        )
        harness = _DbusCoreHarness(service)

        self.assertEqual(
            harness._staged_values_for_enqueue({"/A": 1, "/B": 2}, 11.0, None, False),
            [("/B", 2)],
        )
        self.assertEqual(
            harness._staged_fields_for_enqueue(
                {"mode": 1, "unknown": 2, "start_stop": 1},
                {
                    EVCS_FIELD_TO_PATH["mode"]: 1,
                    EVCS_FIELD_TO_PATH["start_stop"]: 1,
                },
                11.0,
                None,
                False,
            ),
            [("start_stop", 1)],
        )
        self.assertEqual(
            harness._field_items_to_path_items([("mode", 2), ("unknown", 3), ("start_stop", 1)]),
            [(EVCS_FIELD_TO_PATH["mode"], 2), (EVCS_FIELD_TO_PATH["start_stop"], 1)],
        )

    def test_staged_value_and_field_helpers_pass_exact_decision_arguments(self) -> None:
        harness = _DbusCoreHarness(_service())
        with patch.object(harness, "_publish_decision", return_value=(True, None)) as decision:
            self.assertEqual(harness._staged_values_for_enqueue({"/A": 1}, 12.0, 3.0, True), [("/A", 1)])
        decision.assert_called_once_with("/A", 1, 12.0, 3.0, True)

        with patch.object(harness, "_publish_decision", return_value=(True, None)) as decision:
            self.assertEqual(
                harness._staged_fields_for_enqueue(
                    {"mode": 2},
                    {EVCS_FIELD_TO_PATH["mode"]: 2},
                    13.0,
                    4.0,
                    True,
                ),
                [("mode", 2)],
            )
        decision.assert_called_once_with(EVCS_FIELD_TO_PATH["mode"], 2, 13.0, 4.0, True)

    def test_service_value_snapshot_reports_access_operation_and_missing_paths(self) -> None:
        service = _service(_dbusservice={"/A": 1}, _assert_dbus_mainloop_thread=MagicMock())
        harness = _DbusCoreHarness(service)

        self.assertEqual(harness._service_value_snapshot("/A"), (True, 1))
        self.assertEqual(harness._service_value_snapshot("/Missing"), (False, None))
        service._assert_dbus_mainloop_thread.assert_any_call("snapshot /A")
        service._assert_dbus_mainloop_thread.assert_any_call("snapshot /Missing")

    def test_apply_staged_publish_values_stops_at_first_failed_path(self) -> None:
        service = _service(_dbusservice=_FailingDbusService(), _assert_dbus_mainloop_thread=MagicMock())
        harness = _DbusCoreHarness(service)

        changed, published, failed = harness._apply_staged_publish_values(
            [("/Ok", "yes"), ("/Fail", "bad"), ("/Later", "never")],
            55.0,
        )

        self.assertTrue(changed)
        self.assertEqual(published, ["/Ok"])
        self.assertEqual(failed, "/Fail")
        self.assertEqual(service._dbusservice, {"/Ok": "yes"})
        self.assertEqual(service._dbus_publish_state["/Ok"], {"value": "yes", "updated_at": 55.0})
        service._assert_dbus_mainloop_thread.assert_any_call("publish /Ok")
        service._assert_dbus_mainloop_thread.assert_any_call("publish /Fail")

        self.assertEqual(harness._apply_staged_publish_values([], 56.0), (False, [], None))

    def test_restore_helpers_restore_state_delete_new_paths_and_ignore_restore_errors(self) -> None:
        service = _service(
            _dbusservice=_FailingDbusService({"/Old": "changed", "/New": "temporary"}),
            _dbus_publish_state={
                "/Keep": {"value": "new"},
                "/Remove": {"value": "new"},
            },
            _assert_dbus_mainloop_thread=MagicMock(),
        )
        harness = _DbusCoreHarness(service)

        harness._restore_group_publish_state({"/Keep": {"value": "old"}, "/Remove": None})
        harness._restore_service_values(
            ["/Old", "/New", "/Fail", "/Missing"],
            {"/Old": (True, "old"), "/New": (False, None), "/Fail": (True, "ignored")},
        )

        self.assertEqual(service._dbus_publish_state, {"/Keep": {"value": "old"}})
        self.assertEqual(service._dbusservice, {"/Old": "old"})
        service._assert_dbus_mainloop_thread.assert_any_call("restore /Old")
        service._assert_dbus_mainloop_thread.assert_any_call("delete /New")
        service._assert_dbus_mainloop_thread.assert_any_call("restore /Fail")
        service._assert_dbus_mainloop_thread.assert_any_call("delete /Missing")

        harness._restore_group_publish_state({"/AlreadyMissing": None})
        self.assertNotIn("/AlreadyMissing", service._dbus_publish_state)

    def test_publish_group_failure_uses_throttled_warning_or_logging_fallback(self) -> None:
        service = _service(_mark_failure=MagicMock(), _warning_throttled=MagicMock())
        _DbusCoreHarness(service)._publish_group_failure("diag", ["/A", "/B"], 66.0)

        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once_with(
            "dbus-publish-diag-failed",
            1.0,
            "DBus publish group %s failed for paths %s",
            "diag",
            "/A,/B",
        )

        with self.assertLogs(level="WARNING") as logs:
            _DbusCoreHarness(_service())._publish_group_failure("plain", ["/C"], 67.0)
        self.assertEqual(logs.output, ["WARNING:root:DBus publish group plain failed for paths /C at 67.000"])

        with self.assertLogs(level="WARNING") as logs:
            _DbusCoreHarness(_service())._publish_group_failure("plain", ["/C", "/D"], 68.0)
        self.assertEqual(logs.output, ["WARNING:root:DBus publish group plain failed for paths /C,/D at 68.000"])

    def test_transactional_publish_rolls_back_service_values_and_publish_state_on_failure(self) -> None:
        service = _service(
            _dbusservice=_FailingDbusService({"/Ok": "old"}),
            _dbus_publish_state={
                "/Ok": {"value": "old-state", "updated_at": 1.0},
                "/Fail": {"value": "fail-state", "updated_at": 1.0},
            },
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        harness = _DbusCoreHarness(service)

        self.assertFalse(
            harness._publish_values_transactional(
                "rollback",
                {"/Ok": "new", "/Fail": "boom"},
                now=30.0,
                force=True,
            )
        )

        self.assertEqual(service._dbusservice["/Ok"], "old")
        self.assertEqual(service._dbus_publish_state["/Ok"], {"value": "old-state", "updated_at": 1.0})
        self.assertEqual(service._dbus_publish_state["/Fail"], {"value": "fail-state", "updated_at": 1.0})
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()

    def test_live_and_energy_publish_methods_use_semantic_gateway_contract(self) -> None:
        service = _service()
        harness = _DbusCoreHarness(service)
        phase_data = {
            "L1": {"power": 111.0, "current": 1.1, "voltage": 229.1},
            "L2": {"power": 222.0, "current": 2.2, "voltage": 229.2},
            "L3": {"power": 333.0, "current": 3.3, "voltage": 229.3},
        }

        with patch.object(harness, "_publish_fields_transactional", return_value=True) as publish_fields:
            self.assertTrue(harness.publish_live_measurements(666.0, 230.0, 6.6, phase_data, now=40.0))
        publish_fields.assert_called_once_with(
            "live-measurements",
            {
                "ac_power_w": 666.0,
                "ac_voltage_v": 230.0,
                "ac_current_a": 6.6,
                "charge_current_a": 6.6,
                "l1_power_w": 111.0,
                "l1_current_a": 1.1,
                "l1_voltage_v": 229.1,
                "l2_power_w": 222.0,
                "l2_current_a": 2.2,
                "l2_voltage_v": 229.2,
                "l3_power_w": 333.0,
                "l3_current_a": 3.3,
                "l3_voltage_v": 229.3,
            },
            40.0,
            interval_seconds=1.0,
        )

        with patch.object(harness, "_publish_fields_transactional", return_value=True) as publish_fields:
            self.assertTrue(
                harness.publish_energy_time_measurements(
                    12.5,
                    {"L1": 1.1, "L2": 2.2, "L3": 3.3},
                    45,
                    4.5,
                    now=45.0,
                )
            )
        publish_fields.assert_called_once_with(
            "energy-time",
            {
                "energy_forward_kwh": 12.5,
                "l1_energy_forward_kwh": 1.1,
                "l2_energy_forward_kwh": 2.2,
                "l3_energy_forward_kwh": 3.3,
                "charging_time_s": 45,
                "session_energy_kwh": 4.5,
                "session_time_s": 45,
            },
            45.0,
            interval_seconds=5.0,
        )

    def test_live_and_energy_publish_write_expected_dbus_paths(self) -> None:
        service = _service()
        harness = _DbusCoreHarness(service)
        phase_data = {
            "L1": {"power": 111.0, "current": 1.1, "voltage": 229.1},
            "L2": {"power": 222.0, "current": 2.2, "voltage": 229.2},
            "L3": {"power": 333.0, "current": 3.3, "voltage": 229.3},
        }

        self.assertTrue(harness.publish_live_measurements(666.0, 230.0, 6.6, phase_data, now=50.0))
        self.assertEqual(service._dbusservice[EVCS_FIELD_TO_PATH["ac_power_w"]], 666.0)
        self.assertEqual(service._dbusservice[EVCS_FIELD_TO_PATH["l2_current_a"]], 2.2)
        self.assertEqual(service._dbusservice[EVCS_FIELD_TO_PATH["l3_voltage_v"]], 229.3)

        self.assertTrue(
            harness.publish_energy_time_measurements(
                12.5,
                {"L1": 1.1, "L2": 2.2, "L3": 3.3},
                45,
                4.5,
                now=60.0,
            )
        )
        self.assertEqual(service._dbusservice[EVCS_FIELD_TO_PATH["energy_forward_kwh"]], 12.5)
        self.assertEqual(service._dbusservice[EVCS_FIELD_TO_PATH["l3_energy_forward_kwh"]], 3.3)
        self.assertEqual(service._dbusservice[EVCS_FIELD_TO_PATH["session_time_s"]], 45)

    def test_bump_update_index_wraps_and_enqueues_when_mainloop_queue_is_active(self) -> None:
        enqueue_bump = MagicMock()
        queued_service = _service(
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=MagicMock(),
            _enqueue_dbus_update_index_bump=enqueue_bump,
        )
        _DbusCoreHarness(queued_service).bump_update_index(70.0)
        enqueue_bump.assert_called_once_with(70.0)

        direct_service = _service(
            _dbusservice={EVCS_FIELD_TO_PATH["update_index"]: 254},
            _assert_dbus_mainloop_thread=MagicMock(),
        )
        _DbusCoreHarness(direct_service).bump_update_index(71.0)
        self.assertEqual(direct_service._dbusservice[EVCS_FIELD_TO_PATH["update_index"]], 255)
        self.assertEqual(
            direct_service._dbus_publish_state[EVCS_FIELD_TO_PATH["update_index"]],
            {"value": 255, "updated_at": 71.0},
        )
        direct_service._assert_dbus_mainloop_thread.assert_called_once_with(
            f"bump {EVCS_FIELD_TO_PATH['update_index']}"
        )

        wrap_service = _service(_dbusservice={EVCS_FIELD_TO_PATH["update_index"]: 255})
        _DbusCoreHarness(wrap_service).bump_update_index(71.5)
        self.assertEqual(wrap_service._dbusservice[EVCS_FIELD_TO_PATH["update_index"]], 0)

        fallback_service = _service(
            _dbusservice={EVCS_FIELD_TO_PATH["update_index"]: 7},
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=MagicMock(),
            _enqueue_dbus_update_index_bump=None,
        )
        _DbusCoreHarness(fallback_service).bump_update_index(72.0)
        self.assertEqual(fallback_service._dbusservice[EVCS_FIELD_TO_PATH["update_index"]], 8)

        missing_bump_service = _service(
            _dbusservice={EVCS_FIELD_TO_PATH["update_index"]: 8},
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=MagicMock(),
        )
        _DbusCoreHarness(missing_bump_service).bump_update_index(73.0)
        self.assertEqual(missing_bump_service._dbusservice[EVCS_FIELD_TO_PATH["update_index"]], 9)


if __name__ == "__main__":
    unittest.main()
