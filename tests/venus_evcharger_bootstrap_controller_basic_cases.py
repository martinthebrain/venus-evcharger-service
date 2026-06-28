# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_support import (
    MagicMock,
    ServiceBootstrapControllerTestCase,
    SimpleNamespace,
    _install_signal_logging,
    _logging_level_from_config,
    configparser,
    patch,
)


class TestServiceBootstrapControllerBasics(ServiceBootstrapControllerTestCase):
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

        with patch("venus_evcharger.bootstrap.controller.time.sleep") as sleep_mock:
            with patch("venus_evcharger.bootstrap.controller.logging.warning") as warning_mock:
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
