# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_support import *


class TestShellyIoControllerPrimary(ShellyIoControllerTestBase):
    def test_controller_stores_service_and_runtime_now_uses_numeric_clock_only(self):
        service = SimpleNamespace(_time_now=lambda: 123.5)
        controller = ShellyIoController(service)

        self.assertIs(controller.service, service)
        self.assertEqual(controller._runtime_now(), 123.5)
        self.assertEqual(ShellyIoController(SimpleNamespace(_time_now=lambda: 12))._runtime_now(), 12.0)
        self.assertEqual(ShellyIoController(SimpleNamespace(_time_now=lambda: True))._runtime_now(), 0.0)
        self.assertEqual(ShellyIoController(SimpleNamespace(_time_now=lambda: "bad"))._runtime_now(), 0.0)
        self.assertEqual(ShellyIoController(SimpleNamespace(_time_now=123.5))._runtime_now(), 0.0)
        self.assertEqual(ShellyIoController(SimpleNamespace())._runtime_now(), 0.0)

    def test_request_auth_kwargs_supports_digest_basic_and_no_auth(self):
        digest_service = SimpleNamespace(use_digest_auth=True, username="user", password="pass")
        basic_service = SimpleNamespace(use_digest_auth=False, username="user", password="pass")
        none_service = SimpleNamespace(use_digest_auth=False, username="", password="")
        username_only_service = SimpleNamespace(use_digest_auth=False, username="user", password="")

        digest_controller = ShellyIoController(digest_service)
        basic_controller = ShellyIoController(basic_service)
        none_controller = ShellyIoController(none_service)
        username_only_controller = ShellyIoController(username_only_service)

        with patch("venus_evcharger.backend.shelly_io_requests.HTTPDigestAuth", return_value="digest-auth") as digest_auth:
            self.assertEqual(digest_controller._request_auth_kwargs(), {"auth": "digest-auth"})
        digest_auth.assert_called_once_with("user", "pass")
        self.assertEqual(basic_controller._request_auth_kwargs(), {"auth": ("user", "pass")})
        self.assertEqual(none_controller._request_auth_kwargs(), {})
        self.assertEqual(username_only_controller._request_auth_kwargs(), {})

    def test_request_helpers_use_timeout_and_auth_kwargs(self):
        response = MagicMock()
        response.json.return_value = {"ok": True}
        session = MagicMock()
        session.get.return_value = response
        service = SimpleNamespace(
            session=session,
            shelly_request_timeout_seconds=1.5,
            use_digest_auth=False,
            username="",
            password="",
        )
        controller = ShellyIoController(service)

        self.assertEqual(controller.request("http://example.invalid"), {"ok": True})
        self.assertEqual(controller.request_with_session(session, "http://example.invalid/worker"), {"ok": True})

        session.get.assert_any_call(url="http://example.invalid", timeout=1.5)
        session.get.assert_any_call(url="http://example.invalid/worker", timeout=1.5)
        self.assertEqual(controller._request_kwargs("http://example.invalid"), {"url": "http://example.invalid", "timeout": 1.5})
        self.assertEqual(
            ShellyIoController(SimpleNamespace(use_digest_auth=False, username="", password=""))._request_kwargs(
                "http://example.invalid/default"
            ),
            {"url": "http://example.invalid/default", "timeout": 2.0},
        )
        self.assertEqual(controller._json_object({1: "one"}), {"1": "one"})
        with self.assertRaises(ValueError) as error_context:
            controller._json_object(["not", "an", "object"])
        self.assertEqual(str(error_context.exception), "Shelly response must be a JSON object")

    def test_rpc_call_encodes_bool_query_as_lowercase(self):
        service = SimpleNamespace(
            host="192.168.178.76",
            _request=MagicMock(return_value={"ok": True}),
        )

        controller = ShellyIoController(service)
        controller.rpc_call("Switch.Set", id=0, on=False)

        service._request.assert_called_once_with("http://192.168.178.76/rpc/Switch.Set?id=0&on=false")

    def test_rpc_url_without_params_uses_plain_endpoint(self):
        service = SimpleNamespace(host="192.168.178.76")
        controller = ShellyIoController(service)
        self.assertEqual(controller._rpc_url("Switch.GetStatus", None), "http://192.168.178.76/rpc/Switch.GetStatus")

    def test_rpc_call_with_session_and_component_helpers_use_expected_methods(self):
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=2,
            _request_with_session=MagicMock(return_value={"ok": True}),
            rpc_call=MagicMock(return_value={"ison": True}),
            _rpc_call_with_session=MagicMock(return_value={"apower": 1200.0}),
            _worker_session="worker-session",
        )
        controller = ShellyIoController(service)

        self.assertEqual(
            controller.rpc_call_with_session("session", "Switch.Set", id=2, on=True),
            {"ok": True},
        )
        controller.fetch_pm_status()
        controller.set_relay(False)
        controller.worker_fetch_pm_status()

        service._request_with_session.assert_called_once_with(
            "session",
            "http://192.168.178.76/rpc/Switch.Set?id=2&on=true",
        )
        service.rpc_call.assert_any_call("Switch.GetStatus", id=2)
        service.rpc_call.assert_any_call("Switch.Set", id=2, on=False)
        service._rpc_call_with_session.assert_called_once_with("worker-session", "Switch.GetStatus", id=2)

    def test_request_split_paths_forward_runtime_time_and_charger_state(self):
        service = SimpleNamespace()
        controller = ShellyIoController(service)
        charger_state = ChargerState(enabled=True, current_amps=6.0, phase_selection="P1")
        controller._runtime_now = MagicMock(return_value=123.0)
        controller._read_charger_state_best_effort = MagicMock(return_value=charger_state)
        controller._uses_split_backends = MagicMock(return_value=True)
        controller._read_split_pm_status = MagicMock(return_value={"split": True})
        controller.fetch_pm_status_rpc = MagicMock(return_value={"rpc": True})

        self.assertEqual(controller.fetch_pm_status(), {"split": True})

        controller._read_charger_state_best_effort.assert_called_once_with(now=123.0)
        controller._read_split_pm_status.assert_called_once_with(charger_state, now=123.0)
        controller.fetch_pm_status_rpc.assert_not_called()

        controller._runtime_now.reset_mock()
        controller._read_charger_state_best_effort.reset_mock()
        controller._read_split_pm_status.reset_mock()
        controller.worker_fetch_pm_status_rpc = MagicMock(return_value={"worker-rpc": True})

        self.assertEqual(controller.worker_fetch_pm_status(), {"split": True})

        controller._read_charger_state_best_effort.assert_called_once_with(now=123.0)
        controller._read_split_pm_status.assert_called_once_with(charger_state, now=123.0)
        controller.worker_fetch_pm_status_rpc.assert_not_called()

    def test_set_relay_rpc_preserves_true_state_without_split_backend(self):
        service = SimpleNamespace(pm_id=2, rpc_call=MagicMock(return_value={"output": True}))
        controller = ShellyIoController(service)
        controller._split_enable_backend = MagicMock(return_value=None)

        self.assertEqual(controller.set_relay(True), {"output": True})

        service.rpc_call.assert_called_once_with("Switch.Set", id=2, on=True)

    def test_fetch_pm_status_uses_split_backends_and_prefers_switch_state(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=False,
                    power_w=2300.0,
                    voltage_v=230.0,
                    current_a=10.0,
                    energy_kwh=12.5,
                    phase_selection="P1",
                    phase_powers_w=(2300.0, 0.0, 0.0),
                    phase_currents_a=(10.0, 0.0, 0.0),
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=True, phase_selection="P1_P2")),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2"))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            rpc_call=MagicMock(),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertEqual(
            pm_status,
            {
                "output": True,
                "apower": 2300.0,
                "current": 10.0,
                "voltage": 230.0,
                "aenergy": {"total": 12500.0},
                "_phase_selection": "P1",
                "_phase_powers_w": (2300.0, 0.0, 0.0),
                "_phase_currents_a": (10.0, 0.0, 0.0),
            },
        )
        meter_backend.read_meter.assert_called_once_with()
        switch_backend.read_switch_state.assert_called_once_with()
        switch_backend.capabilities.assert_called_once_with()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertIsNone(getattr(service, "_last_switch_feedback_closed", None))
        self.assertIsNone(getattr(service, "_last_switch_interlock_ok", None))

    def test_fetch_pm_status_remembers_optional_switch_feedback_runtime_state(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=True,
                    power_w=1200.0,
                    voltage_v=230.0,
                    current_a=5.2,
                    energy_kwh=2.5,
                    phase_selection="P1",
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=True,
                    phase_selection="P1",
                    feedback_closed=False,
                    interlock_ok=True,
                )
            ),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1",))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            rpc_call=MagicMock(),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _time_now=lambda: 100.0,
        )

        controller = ShellyIoController(service)
        controller.fetch_pm_status()

        self.assertFalse(service._last_switch_feedback_closed)
        self.assertTrue(service._last_switch_interlock_ok)
        self.assertEqual(service._last_switch_feedback_at, 100.0)

    def test_queue_relay_command_warns_when_direct_switch_breaks_over_limit_load(self):
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="direct",
                    max_direct_switch_power_w=1500.0,
                )
            )
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=switch_backend,
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _relay_command_lock=threading.Lock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            relay_sync_timeout_seconds=2.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _last_pm_status={"apower": 1800.0},
            _last_pm_status_confirmed=True,
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=30.0,
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(False)

        service._warning_throttled.assert_called_once()
        self.assertEqual(service._warning_throttled.call_args.args[0], "direct-switch-under-load")
        self.assertFalse(service._pending_relay_state)

    def test_queue_relay_command_skips_overload_warning_for_contactor_switching(self):
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="contactor",
                    max_direct_switch_power_w=1500.0,
                )
            )
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _switch_backend=switch_backend,
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _relay_command_lock=threading.Lock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            relay_sync_timeout_seconds=2.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _last_pm_status={"apower": 1800.0},
            _last_pm_status_confirmed=True,
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=30.0,
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(False)

        service._warning_throttled.assert_not_called()
        self.assertFalse(service._pending_relay_state)

    def test_fetch_pm_status_preserves_legacy_single_phase_line_mapping_in_split_mode(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=True,
                    power_w=2300.0,
                    voltage_v=230.0,
                    current_a=10.0,
                    energy_kwh=12.5,
                    phase_selection="P1",
                    phase_powers_w=(0.0, 2300.0, 0.0),
                    phase_currents_a=(0.0, 10.0, 0.0),
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=True)),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1",))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertEqual(pm_status["_phase_powers_w"], (0.0, 2300.0, 0.0))
        self.assertEqual(pm_status["_phase_currents_a"], (0.0, 10.0, 0.0))
        self.assertEqual(service.active_phase_selection, "P1")

    def test_fetch_pm_status_split_falls_back_to_meter_relay_state_when_switch_read_fails(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=True,
                    power_w=1200.0,
                    voltage_v=230.0,
                    current_a=5.2,
                    energy_kwh=1.5,
                    phase_selection="P1",
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(side_effect=RuntimeError("switch down")),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2_P3"))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_bundle=_runtime_bundle("split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        pm_status = controller.worker_fetch_pm_status()

        self.assertTrue(pm_status["output"])
        self.assertEqual(pm_status["aenergy"]["total"], 1500.0)
        switch_backend.read_switch_state.assert_called_once_with()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
        self.assertEqual(service.requested_phase_selection, "P1_P2_P3")
        self.assertEqual(service.active_phase_selection, "P1")
