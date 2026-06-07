# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_support import *


class _TestShellyIoControllerTertiaryPart2:
    def test_shelly_error_classification_covers_common_network_failures(self):
        controller = ShellyIoController(SimpleNamespace())

        self.assertEqual(controller._classify_shelly_error(requests.exceptions.ConnectTimeout()), "connect-timeout")
        self.assertEqual(controller._classify_shelly_error(requests.exceptions.ReadTimeout()), "read-timeout")
        self.assertEqual(controller._classify_shelly_error(requests.exceptions.Timeout()), "timeout")
        self.assertEqual(
            controller._classify_shelly_error(requests.exceptions.ConnectionError("No route to host")),
            "no-route",
        )
        self.assertEqual(
            controller._classify_shelly_error(requests.exceptions.ConnectionError("Connection refused")),
            "connection-refused",
        )
        self.assertEqual(
            controller._classify_shelly_error(requests.exceptions.ConnectionError("reset by peer")),
            "connection-error",
        )
        self.assertEqual(controller._classify_shelly_error(json.JSONDecodeError("bad", "{}", 0)), "bad-json")
        response = SimpleNamespace(status_code=401)
        self.assertEqual(controller._classify_shelly_error(requests.exceptions.HTTPError(response=response)), "auth-error")
        response = SimpleNamespace(status_code=500)
        self.assertEqual(controller._classify_shelly_error(requests.exceptions.HTTPError(response=response)), "http-error")
        self.assertEqual(controller._classify_shelly_error(ValueError("program error")), "error")

    def test_shelly_retry_state_helpers_cover_fallbacks_and_invalid_values(self):
        controller = ShellyIoController(SimpleNamespace())
        self.assertEqual(controller._shelly_retry_after_value(SimpleNamespace(_shelly_retry_after=True)), 0.0)
        self.assertEqual(
            controller._shelly_retry_after_value(
                SimpleNamespace(_shelly_retry_after="bad", _source_retry_after={"shelly": 123.0})
            ),
            123.0,
        )
        self.assertEqual(
            controller._shelly_retry_after_value(
                SimpleNamespace(_shelly_retry_after=None, _source_retry_after={"shelly": "bad"})
            ),
            0.0,
        )
        self.assertFalse(controller._shelly_retry_active(100.0))
        self.assertTrue(
            ShellyIoController(SimpleNamespace(_shelly_retry_after=None, _source_retry_after={"shelly": 101.0}))
            ._shelly_retry_active(100.0)
        )
        self.assertEqual(controller._shelly_retry_delay_seconds("auth-error", 1), 60.0)

    def test_remember_shelly_failure_covers_invalid_counter_and_source_retry_dict(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=10.0,
            _shelly_consecutive_errors="bad",
            _shelly_offline_since=90.0,
            _source_retry_after={},
            _worker_session=SimpleNamespace(),
            _meter_backend=SimpleNamespace(),
            _switch_backend=SimpleNamespace(reset_transport_session=MagicMock()),
            _charger_backend=SimpleNamespace(),
        )
        controller = ShellyIoController(service)

        with patch("venus_evcharger.backend.shelly_io_worker_transport.requests.Session", return_value=MagicMock()):
            controller._remember_shelly_failure(
                "auth-error",
                "read",
                requests.exceptions.ConnectionError("reset"),
                100.0,
            )

        self.assertEqual(service._shelly_consecutive_errors, 1)
        self.assertEqual(service._shelly_offline_since, 90.0)
        self.assertEqual(service._source_retry_after["shelly"], service._shelly_retry_after)
        service._switch_backend.reset_transport_session.assert_not_called()

    def test_reset_shelly_worker_session_covers_invalid_reset_count(self):
        service = SimpleNamespace(
            _worker_session=SimpleNamespace(),
            session=SimpleNamespace(),
            _meter_backend=SimpleNamespace(reset_transport_session=None),
            _switch_backend=None,
            _charger_backend=None,
            _shelly_session_reset_count="bad",
        )
        controller = ShellyIoController(service)

        with patch("venus_evcharger.backend.shelly_io_worker_transport.requests.Session", side_effect=[MagicMock(), MagicMock()]):
            controller._reset_shelly_worker_session()

        self.assertEqual(service._shelly_session_reset_count, 1)

    def test_io_worker_once_classifies_no_route_sets_backoff_and_resets_session(self):
        old_session = MagicMock()
        new_session = MagicMock()
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(side_effect=requests.exceptions.ConnectionError("No route to host")),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
            _source_retry_ready=MagicMock(return_value=True),
            _source_retry_remaining=MagicMock(return_value=30),
            _delay_source_retry=MagicMock(),
            _worker_session=old_session,
            _shelly_consecutive_errors=0,
            _shelly_offline_since=None,
            _shelly_session_reset_count=0,
        )
        controller = ShellyIoController(service)

        with patch("venus_evcharger.backend.shelly_io_worker.requests.Session", return_value=new_session):
            controller.io_worker_once()

        service._delay_source_retry.assert_called_once_with("shelly", 100.0, 30.0)
        old_session.close.assert_called_once_with()
        self.assertIs(service._worker_session, new_session)
        self.assertEqual(service._shelly_state, "offline")
        self.assertEqual(service._shelly_last_error_reason, "no-route")
        self.assertEqual(service._shelly_consecutive_errors, 1)
        self.assertEqual(service._shelly_session_reset_count, 1)
        self.assertIsNone(service._warning_throttled.call_args.kwargs.get("exc_info"))

    def test_shelly_transport_failure_resets_split_backend_sessions(self):
        old_worker_session = MagicMock()
        old_service_session = MagicMock()
        new_worker_session = MagicMock()
        new_service_session = MagicMock()
        meter_backend = SimpleNamespace(reset_transport_session=MagicMock())
        switch_backend = SimpleNamespace(reset_transport_session=MagicMock())
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(side_effect=requests.exceptions.ConnectTimeout("timeout")),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
            _source_retry_ready=MagicMock(return_value=True),
            _source_retry_remaining=MagicMock(return_value=2),
            _delay_source_retry=MagicMock(),
            _worker_session=old_worker_session,
            session=old_service_session,
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            _charger_backend=switch_backend,
            _shelly_consecutive_errors=0,
            _shelly_offline_since=None,
            _shelly_session_reset_count=0,
        )
        controller = ShellyIoController(service)

        with patch(
            "venus_evcharger.backend.shelly_io_worker.requests.Session",
            side_effect=[new_worker_session, new_service_session],
        ):
            controller.io_worker_once()

        old_worker_session.close.assert_called_once_with()
        old_service_session.close.assert_called_once_with()
        self.assertIs(service._worker_session, new_worker_session)
        self.assertIs(service.session, new_service_session)
        meter_backend.reset_transport_session.assert_called_once_with(new_service_session)
        switch_backend.reset_transport_session.assert_called_once_with(new_service_session)

    def test_io_worker_once_classifies_bad_json_without_transport_session_reset(self):
        old_session = MagicMock()
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(side_effect=json.JSONDecodeError("broken", "{}", 0)),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
            _source_retry_ready=MagicMock(return_value=True),
            _source_retry_remaining=MagicMock(return_value=1),
            _delay_source_retry=MagicMock(),
            _worker_session=old_session,
            _shelly_consecutive_errors=0,
            _shelly_offline_since=None,
            _shelly_session_reset_count=0,
        )
        controller = ShellyIoController(service)

        controller.io_worker_once()

        service._delay_source_retry.assert_called_once_with("shelly", 100.0, 1.0)
        old_session.close.assert_not_called()
        self.assertIs(service._worker_session, old_session)
        self.assertEqual(service._shelly_state, "degraded")
        self.assertEqual(service._shelly_last_error_reason, "bad-json")

    def test_worker_skips_shelly_read_and_pending_relay_while_retry_backoff_is_active(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            _source_retry_ready=MagicMock(return_value=False),
            _source_retry_after={"shelly": 120.0},
        )
        controller = ShellyIoController(service)

        controller.io_worker_once()

        service._worker_fetch_pm_status.assert_not_called()
        self.assertEqual(service._update_worker_snapshot.call_count, 2)
        self.assertFalse(service._update_worker_snapshot.call_args_list[1].kwargs["pm_confirmed"])

        relay_service = SimpleNamespace(
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _time_now=MagicMock(return_value=100.0),
            _source_retry_ready=MagicMock(return_value=False),
            _rpc_call_with_session=MagicMock(),
            _worker_session=MagicMock(),
        )
        controller = ShellyIoController(relay_service)
        controller.worker_apply_pending_relay_command()

        relay_service._rpc_call_with_session.assert_not_called()

    def test_shelly_success_clears_retry_state(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(side_effect=[100.0, 101.0]),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(return_value={"output": True}),
            _mark_recovery=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            _source_retry_ready=MagicMock(return_value=True),
            _source_retry_after={"shelly": 130.0},
            _shelly_consecutive_errors=4,
            _shelly_retry_after=130.0,
            _shelly_offline_since=90.0,
        )
        controller = ShellyIoController(service)

        controller.io_worker_once()

        self.assertEqual(service._shelly_state, "online")
        self.assertEqual(service._shelly_consecutive_errors, 0)
        self.assertEqual(service._shelly_last_ok_at, 101.0)
        self.assertEqual(service._source_retry_after["shelly"], 0.0)
        self.assertIsNone(service._shelly_offline_since)

    def test_io_worker_loop_logs_cycle_failure_and_continues(self):
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, False]
        stop_event.wait.side_effect = [False, True]
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_stop_event=stop_event,
            _time_now=MagicMock(side_effect=[100.0, 100.2, 101.0, 101.1]),
            _worker_poll_interval_seconds=1.0,
            _warning_throttled=MagicMock(),
        )

        controller = ShellyIoController(service)
        controller.io_worker_once = MagicMock(side_effect=[RuntimeError("boom"), None])

        controller.io_worker_loop()

        service._ensure_worker_state.assert_called_once_with()
        self.assertEqual(controller.io_worker_once.call_count, 2)
        service._warning_throttled.assert_called_once()
        args = service._warning_throttled.call_args[0]
        self.assertEqual(args[0], "io-worker-cycle-failed")
        self.assertEqual(args[1], 1.0)
        self.assertEqual(args[2], "Background I/O worker cycle failed: %s")
        self.assertEqual(str(args[3]), "boom")
