# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_support import (
    MagicMock,
    ServiceBootstrapController,
    ServiceBootstrapControllerTestCase,
    SimpleNamespace,
    _FakeGobjectTimers,
    _install_signal_logging,
    _logging_level_from_config,
    configparser,
    patch,
)


class TestServiceBootstrapControllerBasics(ServiceBootstrapControllerTestCase):
    def test_constructor_wires_all_runtime_dependencies(self):
        service = SimpleNamespace()
        normalize_phase = MagicMock(return_value="L1")
        normalize_mode = MagicMock(return_value=2)
        mode_uses_auto_logic = MagicMock(return_value=True)
        month_window = MagicMock(return_value=((7, 0), (19, 0)))
        read_version = MagicMock(return_value="1.2.3")
        gobject_module = _FakeGobjectTimers()
        formatters = {"w": MagicMock(return_value="123 W")}

        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=normalize_phase,
            normalize_mode_func=normalize_mode,
            mode_uses_auto_logic_func=mode_uses_auto_logic,
            month_window_func=month_window,
            read_version_func=read_version,
            gobject_module=gobject_module,
            script_path="/tmp/custom-service.py",
            formatters=formatters,
        )

        self.assertIs(controller.service, service)
        self.assertIs(controller.dependencies.normalize_phase, normalize_phase)
        self.assertIs(controller.dependencies.normalize_mode, normalize_mode)
        self.assertIs(controller.dependencies.mode_uses_auto_logic, mode_uses_auto_logic)
        self.assertIs(controller.dependencies.month_window, month_window)
        self.assertIs(controller.dependencies.read_version, read_version)
        self.assertIs(controller.dependencies.gobject, gobject_module)
        self.assertEqual(controller.dependencies.script_path, "/tmp/custom-service.py")
        self.assertIs(controller.dependencies.formatters, formatters)
        self.assertIs(controller.components.config.identity, controller.components.identity)
        self.assertIs(controller.components.config.backend, controller.components.backend)
        self.assertIs(controller.components.config.auto, controller.components.auto)

    def test_fetch_device_info_with_fallback_returns_empty_dict_after_retries(self):
        service = SimpleNamespace(
            startup_device_info_retries=2,
            startup_device_info_retry_seconds=0,
            fetch_rpc=MagicMock(side_effect=RuntimeError("offline")),
        )

        controller = self._controller(service)
        self.assertEqual(controller.fetch_device_info_with_fallback(), {})
        self.assertEqual(service.fetch_rpc.call_count, 3)

    def test_logging_level_and_signal_install_cover_default_and_error_paths(self):
        empty_config = configparser.ConfigParser(default_section="NOT_DEFAULT")
        self.assertEqual(_logging_level_from_config(empty_config, "WARNING"), "WARNING")

        handlers = {}

        def fake_signal(signum, handler):
            handlers[signum] = handler

        with patch("venus_evcharger.bootstrap.controller.signal.SIGTERM", 15), patch(
            "venus_evcharger.bootstrap.controller.signal.SIGINT", 2
        ), patch("venus_evcharger.bootstrap.controller.signal.SIGHUP", None), patch(
            "venus_evcharger.bootstrap.controller.signal.signal",
            side_effect=fake_signal,
        ):
            _install_signal_logging(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        self.assertEqual(sorted(handlers), [2, 15])
        with patch("venus_evcharger.bootstrap.controller.logging.debug") as debug_mock:
            handlers[15](15, None)
        debug_mock.assert_called_once()

    def test_fetch_device_info_with_fallback_logs_retry_and_sleeps(self):
        service = SimpleNamespace(
            startup_device_info_retries=1,
            startup_device_info_retry_seconds=2.5,
            fetch_rpc=MagicMock(side_effect=[RuntimeError("offline"), {"mac": "ABC"}]),
        )
        controller = self._controller(service)

        with patch("venus_evcharger.bootstrap.runtime_metadata.time.sleep") as sleep_mock:
            with patch("venus_evcharger.bootstrap.runtime_metadata.logging.warning") as warning_mock:
                result = controller.fetch_device_info_with_fallback()

        self.assertEqual(result, {"mac": "ABC"})
        sleep_mock.assert_called_once_with(2.5)
        self.assertGreaterEqual(warning_mock.call_count, 1)

    def test_fetch_device_info_with_fallback_ignores_non_mapping_payload(self):
        service = SimpleNamespace(
            startup_device_info_retries=0,
            startup_device_info_retry_seconds=0,
            fetch_rpc=MagicMock(return_value=["not", "a", "mapping"]),
        )
        controller = self._controller(service)

        self.assertEqual(controller.fetch_device_info_with_fallback(), {})
