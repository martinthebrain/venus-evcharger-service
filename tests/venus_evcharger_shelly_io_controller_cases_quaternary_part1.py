# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_cases_quaternary_support import *  # noqa: F401,F403

class _TestShellyIoControllerQuaternaryPart1:
    def test_start_io_worker_spawns_thread_only_when_missing_and_always_checks_helper(self):
        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        service = SimpleNamespace(
            _worker_thread=alive_thread,
        )

        controller = ShellyIoController(service)
        with patch("venus_evcharger.backend.shelly_io_worker_lifecycle.threading.Thread") as thread_factory:
            controller.start_io_worker()
            thread_factory.assert_not_called()
        service.runtime.ensure_auto_input_helper.assert_called_once_with()

        service = SimpleNamespace(
            _worker_thread=None,
        )
        controller = ShellyIoController(service)
        thread = MagicMock()
        with patch("venus_evcharger.backend.shelly_io_worker_lifecycle.threading.Thread", return_value=thread) as thread_factory:
            controller.start_io_worker()

        thread_factory.assert_called_once()
        thread_kwargs = thread_factory.call_args.kwargs
        self.assertEqual(thread_kwargs["name"], "shelly-wallbox-shelly-io")
        self.assertTrue(thread_kwargs["daemon"])
        self.assertIs(thread_kwargs["target"].__self__, controller.worker)
        self.assertEqual(thread_kwargs["target"].__func__, controller.worker.io_worker_loop.__func__)
        thread.start.assert_called_once_with()
        service.runtime.ensure_auto_input_helper.assert_called_once_with()

    def test_worker_stale_restart_seconds_uses_each_threshold_source(self):
        self.assertEqual(
            ShellyWorkerLifecycle._runtime_seconds_setting(SimpleNamespace(), "missing", 3.5),
            3.5,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._runtime_seconds_setting(SimpleNamespace(value=None), "value", 3.5),
            3.5,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._runtime_seconds_setting(SimpleNamespace(value=0.0), "value", 3.5),
            3.5,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._runtime_seconds_setting(SimpleNamespace(value="4.25"), "value", 3.5),
            4.25,
        )
        with self.assertRaises(ValueError):
            ShellyWorkerLifecycle._runtime_seconds_setting(SimpleNamespace(value="bad"), "value", 3.5)

        self.assertEqual(
            ShellyWorkerLifecycle._worker_stale_restart_seconds(SimpleNamespace()),
            6.0,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._worker_stale_restart_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=0.1,
                    shelly_request_timeout_seconds=0.1,
                    relay_sync_timeout_seconds=0.1,
                )
            ),
            5.0,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._worker_stale_restart_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=0.0,
                    shelly_request_timeout_seconds=0.1,
                    relay_sync_timeout_seconds=0.1,
                )
            ),
            5.0,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._worker_stale_restart_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=0.2,
                    shelly_request_timeout_seconds=0.1,
                )
            ),
            5.0,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._worker_stale_restart_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=3.0,
                    shelly_request_timeout_seconds=1.0,
                    relay_sync_timeout_seconds=1.0,
                )
            ),
            15.0,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._worker_stale_restart_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=0.2,
                    shelly_request_timeout_seconds=4.0,
                    relay_sync_timeout_seconds=1.0,
                )
            ),
            12.0,
        )
        self.assertEqual(
            ShellyWorkerLifecycle._worker_stale_restart_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=0.2,
                    shelly_request_timeout_seconds=1.0,
                    relay_sync_timeout_seconds=4.0,
                )
            ),
            8.0,
        )

    def test_worker_snapshot_age_clamps_future_snapshot_and_preserves_elapsed_time(self):
        runtime = SimpleNamespace(worker_snapshot=MagicMock(return_value={"captured_at": 90.0}))
        service = SimpleNamespace(runtime=runtime)
        self.assertEqual(ShellyWorkerLifecycle._worker_snapshot_age(service, 100.0), 10.0)

        runtime.worker_snapshot = MagicMock(return_value={"captured_at": 101.0})
        self.assertEqual(ShellyWorkerLifecycle._worker_snapshot_age(service, 100.0), 0.0)

    def test_worker_thread_stale_respects_alive_snapshot_and_strict_threshold(self):
        self.assertFalse(ShellyWorkerLifecycle._worker_thread_stale(SimpleNamespace(), 100.0))
        self.assertFalse(ShellyWorkerLifecycle._worker_thread_stale(SimpleNamespace(_worker_thread=None), 100.0))

        dead_thread = MagicMock()
        dead_thread.is_alive.return_value = False
        self.assertFalse(
            ShellyWorkerLifecycle._worker_thread_stale(SimpleNamespace(_worker_thread=dead_thread), 100.0)
        )

        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        base_service = SimpleNamespace(
            _worker_thread=alive_thread,
            _worker_poll_interval_seconds=1.0,
            shelly_request_timeout_seconds=2.0,
            relay_sync_timeout_seconds=2.0,
            runtime=SimpleNamespace(worker_snapshot=MagicMock(return_value={"captured_at": 94.0})),
        )
        self.assertFalse(ShellyWorkerLifecycle._worker_thread_stale(base_service, 100.0))

        base_service.runtime.worker_snapshot = MagicMock(return_value={"captured_at": 93.9})
        self.assertTrue(ShellyWorkerLifecycle._worker_thread_stale(base_service, 100.0))

        with patch.object(ShellyWorkerLifecycle, "_worker_stale_restart_seconds", return_value=6.0) as stale_after:
            self.assertTrue(ShellyWorkerLifecycle._worker_thread_stale(base_service, 100.0))
        stale_after.assert_called_once_with(base_service)

    def test_start_io_worker_restarts_stale_alive_worker_with_fresh_stop_event_and_session(self):
        stale_thread = MagicMock()
        stale_thread.is_alive.return_value = True
        old_stop_event = MagicMock()
        old_session = MagicMock()
        new_stop_event = MagicMock()
        new_session = MagicMock()
        service = SimpleNamespace(
            _worker_thread=stale_thread,
            _worker_stop_event=old_stop_event,
            _worker_session=old_session,
            _worker_poll_interval_seconds=1.0,
            shelly_request_timeout_seconds=2.0,
            relay_sync_timeout_seconds=2.0,
            time_now=MagicMock(return_value=100.0),
        )
        controller = ShellyIoController(service)
        service.runtime.worker_snapshot.return_value = {"captured_at": 90.0}
        new_thread = MagicMock()

        with (
            patch("venus_evcharger.backend.shelly_io_worker_lifecycle.threading.Event", return_value=new_stop_event),
            patch("venus_evcharger.backend.shelly_io_worker_lifecycle.requests.Session", return_value=new_session),
            patch("venus_evcharger.backend.shelly_io_worker_lifecycle.threading.Thread", return_value=new_thread),
        ):
            controller.start_io_worker()

        old_stop_event.set.assert_called_once_with()
        old_session.close.assert_called_once_with()
        self.assertIs(service._worker_stop_event, new_stop_event)
        self.assertIs(service._worker_session, new_session)
        self.assertIs(service._worker_thread, new_thread)
        new_thread.start.assert_called_once_with()
        service.runtime.warning_throttled.assert_called_once_with(
            "io-worker-stale-restart",
            6.0,
            "Background I/O worker stale for %.1fs, restarting worker session",
            10.0,
        )

    def test_start_io_worker_keeps_alive_worker_when_snapshot_read_fails(self):
        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        service = SimpleNamespace(
            _worker_thread=alive_thread,
            _worker_poll_interval_seconds=1.0,
            shelly_request_timeout_seconds=2.0,
            relay_sync_timeout_seconds=2.0,
        )
        controller = ShellyIoController(service)
        service.runtime.worker_snapshot.side_effect = RuntimeError("snapshot unavailable")

        with patch("venus_evcharger.backend.shelly_io_worker_lifecycle.threading.Thread") as thread_factory:
            controller.start_io_worker()

        thread_factory.assert_not_called()
        service.runtime.ensure_auto_input_helper.assert_called_once_with()

    def test_worker_snapshot_age_ignores_invalid_snapshot_shapes(self):
        service = SimpleNamespace(runtime=SimpleNamespace(worker_snapshot=MagicMock(return_value=None)))
        self.assertIsNone(ShellyWorkerLifecycle._worker_snapshot_age(service, 100.0))

        service.runtime.worker_snapshot = MagicMock(return_value={"captured_at": "bad"})
        self.assertIsNone(ShellyWorkerLifecycle._worker_snapshot_age(service, 100.0))

        service.runtime.worker_snapshot = MagicMock(return_value={"captured_at": True})
        self.assertIsNone(ShellyWorkerLifecycle._worker_snapshot_age(service, 100.0))

    def test_restart_stale_io_worker_tolerates_missing_stop_and_close_methods(self):
        service = SimpleNamespace(
            _worker_poll_interval_seconds=1.0,
            shelly_request_timeout_seconds=2.0,
            relay_sync_timeout_seconds=2.0,
            _worker_stop_event=object(),
            _worker_session=object(),
        )
        controller = ShellyIoController(service)
        new_stop_event = MagicMock()
        new_session = MagicMock()

        with (
            patch("venus_evcharger.backend.shelly_io_worker_lifecycle.threading.Event", return_value=new_stop_event),
            patch("venus_evcharger.backend.shelly_io_worker_lifecycle.requests.Session", return_value=new_session),
        ):
            controller.lifecycle._restart_stale_io_worker(100.0)

        service.runtime.warning_throttled.assert_called_once()
        self.assertEqual(service.runtime.warning_throttled.call_args.args[-1], -1.0)
        self.assertIs(service._worker_stop_event, new_stop_event)
        self.assertIs(service._worker_session, new_session)
        self.assertIsNone(service._worker_thread)

    def test_restart_stale_io_worker_handles_missing_runtime_handles(self):
        service = SimpleNamespace(
            _worker_poll_interval_seconds=0.2,
            shelly_request_timeout_seconds=0.1,
            relay_sync_timeout_seconds=0.1,
        )
        controller = ShellyIoController(service)
        service.runtime.worker_snapshot.return_value = {"captured_at": 97.5}
        new_stop_event = MagicMock()
        new_session = MagicMock()

        with (
            patch("venus_evcharger.backend.shelly_io_worker_lifecycle.threading.Event", return_value=new_stop_event),
            patch("venus_evcharger.backend.shelly_io_worker_lifecycle.requests.Session", return_value=new_session),
        ):
            controller.lifecycle._restart_stale_io_worker(100.0)

        service.runtime.warning_throttled.assert_called_once_with(
            "io-worker-stale-restart",
            5.0,
            "Background I/O worker stale for %.1fs, restarting worker session",
            2.5,
        )
        self.assertIs(service._worker_stop_event, new_stop_event)
        self.assertIs(service._worker_session, new_session)
        self.assertIsNone(service._worker_thread)

    def test_helper_edges_cover_phase_vectors_runtime_cache_and_split_retry_paths(self):
        self.assertEqual(_single_phase_vector(9.0, "L2"), (0.0, 9.0, 0.0))
        self.assertEqual(_single_phase_vector(9.0, "L3"), (0.0, 0.0, 9.0))
        self.assertEqual(_phase_powers_for_selection(12.0, "P1_P2"), (6.0, 6.0, 0.0))
        self.assertIsNone(_phase_currents_for_selection(None, "P1"))
        self.assertEqual(_phase_currents_for_selection(12.0, "P1_P2"), (6.0, 6.0, 0.0))

        service = SimpleNamespace(
            use_digest_auth=False,
            username="user",
            password="pass",
            shelly_request_timeout_seconds=2.5,
            _backend_bundle=_runtime_bundle("combined"),
            _switch_backend=SimpleNamespace(capabilities=MagicMock(side_effect=RuntimeError("boom"))),
            supported_phase_selections=("P1", "P1_P2"),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _last_pm_status_confirmed=False,
            _last_pm_status="bad",
            _last_voltage=400.0,
            voltage_mode="line",
            auto_shelly_soft_fail_seconds=15.0,
            time_now=lambda: 100.0,
            _source_retry_after={},
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=120.0,
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            phase="L3",
        )
        controller = ShellyIoController(service)

        self.assertEqual(
            controller.requests._request_kwargs("http://example.invalid"),
            {
                "url": "http://example.invalid",
                "timeout": 2.5,
                "auth": ("user", "pass"),
            },
        )
        self.assertIsNone(controller.capabilities.split_meter_backend())
        self.assertIsNone(controller.capabilities._phase_switch_capabilities())
        self.assertFalse(controller.capabilities._charger_supports_phase_selection("P1"))
        self.assertIsNone(controller.capabilities._current_confirmed_switch_load_power_w())
        self.assertIsNone(controller.capabilities._direct_switch_warning_context(True))
        self.assertAlmostEqual(controller.runtime.estimated_phase_voltage_v("P1_P2"), 400.0 / (3.0**0.5))
        self.assertIsNone(controller.runtime.estimated_charger_power_w(None, "P1"))
        self.assertIsNone(
            controller.runtime_cache.cached_charger_state(now=100.0, max_age_seconds=10.0)
        )
        self.assertEqual(
            controller.runtime.resolved_pm_charger_current(
                ChargerState(enabled=True, current_amps=12.0, phase_selection="P1", status_text="ready")
            ),
            0.0,
        )
        self.assertEqual(
            controller.runtime.resolved_pm_charger_current(
                ChargerState(enabled=False, current_amps=12.0, phase_selection="P1")
            ),
            0.0,
        )
        self.assertIsNone(
            controller.runtime.resolved_pm_charger_current(
                ChargerState(enabled=None, current_amps=None, phase_selection=None)
            )
        )
        self.assertEqual(
            controller.readback._resolved_charger_current(
                ChargerState(enabled=None, current_amps=None, phase_selection=None, actual_current_amps=7.5)
            ),
            7.5,
        )
        self.assertEqual(
            controller.readback._resolved_charger_current(
                ChargerState(enabled=None, current_amps=6.5, phase_selection="P1")
            ),
            6.5,
        )
        self.assertIsNone(
            controller.readback._resolved_charger_current(
                ChargerState(enabled=None, current_amps=None, phase_selection=None)
            )
        )
        self.assertEqual(
            controller.readback._pm_status_from_charger_state(
                ChargerState(enabled=None, current_amps=None, phase_selection=None),
                relay_on=None,
            ),
            {
                "apower": 0.0,
                "aenergy": {"total": 0.0},
                "_phase_selection": "P1",
                "voltage": 400.0,
                "_phase_powers_w": (0.0, 0.0, 0.0),
            },
        )
        self.assertEqual(controller.capabilities.split_switch_supported_phase_selections(), ("P1", "P1_P2"))
        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            controller.set_phase_selection("P1_P2_P3")
        controller.runtime.remember_charger_retry("offline", "read", 100.0)
        service.runtime.delay_source_retry.assert_called_once_with("charger", 100.0, 20.0)
        controller.runtime.clear_charger_retry()
        self.assertEqual(service._source_retry_after["charger"], 0.0)
        self.assertIsNone(service._charger_retry_until)
        service._source_retry_after = None
        controller.runtime.clear_charger_retry()
        self.assertIsNone(service._source_retry_after)
        self.assertEqual(controller.readback._relay_state_from_split_switch(True), True)
        self.assertIsNone(controller.runtime_cache.cached_charger_state())

        config = configparser.ConfigParser()
        config.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        config_backed_service = SimpleNamespace(
            config=config,
            _switch_backend=None,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _last_pm_status_confirmed=False,
            _last_pm_status="bad",
            _last_voltage=400.0,
            voltage_mode="line",
            auto_shelly_soft_fail_seconds=15.0,
            time_now=lambda: 100.0,
            _source_retry_after={},
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
        )
        config_backed_controller = ShellyIoController(config_backed_service)
        self.assertTrue(config_backed_controller.capabilities.uses_split_backends())

        service._last_pm_status_confirmed = True
        service._last_pm_status = "bad"
        self.assertIsNone(controller.capabilities._current_confirmed_switch_load_power_w())
        service._last_pm_status = {"apower": 1000.0}
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="direct",
                    max_direct_switch_power_w=1500.0,
                )
            )
        )
        service._switch_backend = switch_backend
        controller = ShellyIoController(service)
        self.assertIsNone(controller.capabilities._direct_switch_warning_context(False))
        service._last_pm_status = {"apower": 2000.0}
        controller.capabilities.warn_if_direct_switching_under_load(False)
        service.runtime.warning_throttled.assert_called_once()
        self.assertEqual(
            controller.runtime.resolved_pm_charger_current(
                ChargerState(enabled=True, current_amps=12.0, phase_selection="P1")
            ),
            12.0,
        )
        cached_state = ChargerState(enabled=True, current_amps=None, phase_selection=None)
        controller.runtime._store_runtime_charger_snapshot(cached_state, 95.0)
        self.assertIs(controller.runtime_cache.cached_charger_state(max_age_seconds=None), cached_state)
        self.assertIsNone(controller.runtime_cache.cached_charger_state(now=110.0, max_age_seconds=10.0))
        service._readback_store.replace_charger(None)
        self.assertIsNone(controller.runtime_cache.cached_charger_state())
