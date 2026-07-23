# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Callable

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
    def test_constructor_wires_all_runtime_dependencies(self) -> None:
        service = SimpleNamespace()
        normalize_phase = MagicMock(return_value="L1")
        normalize_mode = MagicMock(return_value=2)
        mode_uses_auto_logic = MagicMock(return_value=True)
        month_window = MagicMock(return_value=((7, 0), (19, 0)))
        read_version = MagicMock(return_value="1.2.3")
        gobject_module = _FakeGobjectTimers()

        functions = self._function_bundle(
            normalize_phase=normalize_phase,
            normalize_mode=normalize_mode,
            mode_uses_auto_logic=mode_uses_auto_logic,
            month_window=month_window,
            read_version=read_version,
            gobject=gobject_module,
            script_path="/tmp/custom-service.py",
        )
        owner = self._owner(service, functions)
        controller = owner.bootstrap
        self.assertIsInstance(controller, ServiceBootstrapController)
        assert isinstance(controller, ServiceBootstrapController)

        self.assertIs(controller.service, service)
        self.assertIs(owner.functions, functions)
        self.assertIs(service.controllers, owner)
        self.assertIs(controller.dependencies.normalize_phase, normalize_phase)
        self.assertIs(controller.dependencies.normalize_mode, normalize_mode)
        self.assertIs(controller.dependencies.mode_uses_auto_logic, mode_uses_auto_logic)
        self.assertIs(controller.dependencies.month_window, month_window)
        self.assertIs(controller.dependencies.read_version, read_version)
        self.assertIs(controller.dependencies.gobject, gobject_module)
        self.assertEqual(controller.dependencies.script_path, "/tmp/custom-service.py")
        self.assertIs(controller.components.config.identity, controller.components.identity)
        self.assertIs(controller.components.config.backend, controller.components.backend)
        self.assertIs(controller.components.config.auto, controller.components.auto)

        with patch.object(controller.components.runtime, "prepare_runtime_state") as prepare_runtime_state:
            controller.prepare_runtime_state()
        prepare_runtime_state.assert_called_once_with()

    def test_logging_level_and_signal_install_cover_default_and_error_paths(self) -> None:
        empty_config = configparser.ConfigParser(default_section="NOT_DEFAULT")
        self.assertEqual(_logging_level_from_config(empty_config, "WARNING"), "WARNING")

        handlers: dict[int, Callable[[int, object], None]] = {}

        def fake_signal(signum: int, handler: Callable[[int, object], None]) -> None:
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
