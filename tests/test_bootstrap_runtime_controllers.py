# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from venus_evcharger.bootstrap.runtime_controllers import initialize_runtime_controllers
from venus_evcharger.ports import AutoDecisionPort, UpdateCyclePort, WriteControllerPort


class BootstrapRuntimeControllerWiringContracts(unittest.TestCase):
    def _resolved_backends(self) -> SimpleNamespace:
        return SimpleNamespace(
            runtime=SimpleNamespace(topology_configured=True, primary_rpc_configured=False),
            meter="meter-backend",
            switch="switch-backend",
            charger="charger-backend",
        )

    def test_initialize_runtime_controllers_wires_backends_and_controller_dependencies(self) -> None:
        service = SimpleNamespace(_state_controller=None)
        age_seconds = MagicMock(return_value=0.0)
        health_code = MagicMock(return_value=0)
        mode_uses_auto_logic = MagicMock(return_value=True)
        normalize_mode = MagicMock(return_value=1)
        phase_values = MagicMock(return_value={})
        resolved_backends = self._resolved_backends()

        with (
            patch("venus_evcharger.bootstrap.runtime_controllers.RuntimeSupportController") as runtime_support,
            patch("venus_evcharger.bootstrap.runtime_controllers.AutoDecisionController") as auto_controller,
            patch("venus_evcharger.bootstrap.runtime_controllers.DbusPublishController") as publisher,
            patch("venus_evcharger.bootstrap.runtime_controllers.ShellyIoController") as shelly_io,
            patch("venus_evcharger.bootstrap.runtime_controllers.build_service_backends", return_value=resolved_backends) as build_backends,
            patch("venus_evcharger.bootstrap.runtime_controllers.ServiceStateController") as state_controller,
            patch("venus_evcharger.bootstrap.runtime_controllers.DbusWriteController") as write_controller,
            patch("venus_evcharger.bootstrap.runtime_controllers.AutoInputSupervisor") as input_supervisor,
            patch("venus_evcharger.bootstrap.runtime_controllers.UpdateCycleController") as update_controller,
        ):
            initialize_runtime_controllers(
                service,
                age_seconds=age_seconds,
                health_code=health_code,
                mode_uses_auto_logic=mode_uses_auto_logic,
                normalize_mode=normalize_mode,
                phase_values=phase_values,
            )

        runtime_support.assert_called_once_with(service, age_seconds, health_code)
        runtime_support.return_value.initialize_runtime_support.assert_called_once_with()
        auto_port = auto_controller.call_args.args[0]
        self.assertIsInstance(auto_port, AutoDecisionPort)
        self.assertIs(auto_port._service, service)
        auto_controller.assert_called_once_with(auto_port, health_code, mode_uses_auto_logic)
        publisher.assert_called_once_with(service, age_seconds)
        shelly_io.assert_called_once_with(service)
        build_backends.assert_called_once_with(service)
        state_controller.assert_called_once_with(service, normalize_mode)
        write_port = write_controller.call_args.args[0]
        self.assertIsInstance(write_port, WriteControllerPort)
        self.assertIs(write_port._service, service)
        write_controller.assert_called_once_with(write_port)
        input_supervisor.assert_called_once_with(service)
        update_port = update_controller.call_args.args[0]
        self.assertIsInstance(update_port, UpdateCyclePort)
        self.assertIs(update_port._service, service)
        update_controller.assert_called_once_with(update_port, phase_values, health_code)
        self.assertIs(service._runtime_support_controller, runtime_support.return_value)
        self.assertIs(service._auto_controller, auto_controller.return_value)
        self.assertIs(service._dbus_publisher, publisher.return_value)
        self.assertIs(service._shelly_io_controller, shelly_io.return_value)
        self.assertIs(service._backend_bundle, resolved_backends)
        self.assertEqual(service._meter_backend, "meter-backend")
        self.assertEqual(service._switch_backend, "switch-backend")
        self.assertEqual(service._charger_backend, "charger-backend")
        self.assertIs(service.topology_configured, True)
        self.assertIs(service.primary_rpc_configured, False)
        self.assertIs(service._state_controller, state_controller.return_value)
        self.assertIs(service._write_controller, write_controller.return_value)
        self.assertIs(service._auto_input_supervisor, input_supervisor.return_value)
        self.assertIs(service._update_controller, update_controller.return_value)

    def test_initialize_runtime_controllers_preserves_existing_state_controller(self) -> None:
        existing_state_controller = object()
        service = SimpleNamespace(_state_controller=existing_state_controller)

        with (
            patch("venus_evcharger.bootstrap.runtime_controllers.RuntimeSupportController") as runtime_support,
            patch("venus_evcharger.bootstrap.runtime_controllers.AutoDecisionController"),
            patch("venus_evcharger.bootstrap.runtime_controllers.DbusPublishController"),
            patch("venus_evcharger.bootstrap.runtime_controllers.ShellyIoController"),
            patch("venus_evcharger.bootstrap.runtime_controllers.build_service_backends", return_value=self._resolved_backends()),
            patch("venus_evcharger.bootstrap.runtime_controllers.ServiceStateController") as state_controller,
            patch("venus_evcharger.bootstrap.runtime_controllers.DbusWriteController"),
            patch("venus_evcharger.bootstrap.runtime_controllers.AutoInputSupervisor"),
            patch("venus_evcharger.bootstrap.runtime_controllers.UpdateCycleController"),
        ):
            runtime_support.return_value.initialize_runtime_support = MagicMock()
            initialize_runtime_controllers(
                service,
                age_seconds=MagicMock(),
                health_code=MagicMock(),
                mode_uses_auto_logic=MagicMock(),
                normalize_mode=MagicMock(),
                phase_values=MagicMock(),
            )

        state_controller.assert_not_called()
        self.assertIs(service._state_controller, existing_state_controller)


if __name__ == "__main__":
    unittest.main()
