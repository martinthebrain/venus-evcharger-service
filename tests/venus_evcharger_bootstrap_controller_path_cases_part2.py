# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import TestCase, mock
from unittest.mock import MagicMock, patch

from venus_evcharger.bootstrap.paths import ServicePathRegistrar

from tests.venus_evcharger_bootstrap_controller_support import _FakeDbusService


class _WriteHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def handle_dbus_write(self, path: str, value: object) -> bool:
        self.calls.append((path, value))
        return True


class TestServiceBootstrapPathComposition(TestCase):
    def _registrar(self) -> tuple[ServicePathRegistrar, _FakeDbusService, _WriteHandler]:
        dbus_service = _FakeDbusService()
        write_handler = _WriteHandler()
        service = mock.Mock(_dbusservice=dbus_service, auto=write_handler, virtual_mode=0)
        registrar = ServicePathRegistrar(
            service,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )
        return registrar, dbus_service, write_handler

    def test_register_wires_each_dynamic_path_to_the_explicit_write_port(self) -> None:
        registrar, dbus_service, write_handler = self._registrar()
        formatter = MagicMock()
        with patch("venus_evcharger.bootstrap.paths.management_paths", return_value={}), patch.object(
            registrar,
            "path_map",
            return_value={"/Path/One": (1, formatter), "/Path/Two": ("two", None)},
        ), patch("venus_evcharger.bootstrap.paths.logging.debug") as debug:
            registrar.register()

        self.assertEqual(dbus_service.paths["/Path/One"]["value"], 1)
        self.assertIs(dbus_service.paths["/Path/One"]["gettextcallback"], formatter)
        self.assertEqual(dbus_service.paths["/Path/Two"]["value"], "two")
        self.assertIsNone(dbus_service.paths["/Path/Two"]["gettextcallback"])
        self.assertEqual(len(dbus_service.paths), 2)
        for details in dbus_service.paths.values():
            callback = details["onchangecallback"]
            self.assertEqual(callback.__self__, write_handler)
            self.assertEqual(callback.__func__, write_handler.handle_dbus_write.__func__)
        self.assertEqual(debug.call_count, 2)

    def test_register_logs_and_reraises_dbus_registration_errors(self) -> None:
        registrar, dbus_service, _write_handler = self._registrar()
        failure = RuntimeError("dbus rejected path")
        dbus_service.add_path = MagicMock(side_effect=failure)
        with patch("venus_evcharger.bootstrap.paths.management_paths", return_value={}), patch.object(
            registrar,
            "path_map",
            return_value={"/Broken": (1, None)},
        ), patch("venus_evcharger.bootstrap.paths.logging.error") as error, self.assertRaises(RuntimeError) as raised:
            registrar.register()

        self.assertIs(raised.exception, failure)
        error.assert_called_once_with("Failed to register path %s: %s", "/Broken", failure, exc_info=failure)

    def test_path_map_composes_measurement_control_and_diagnostics(self) -> None:
        registrar, _dbus_service, _write_handler = self._registrar()
        with patch(
            "venus_evcharger.bootstrap.paths.measurement_paths",
            return_value={"/Measurement": (1, None)},
        ) as measurement, patch(
            "venus_evcharger.bootstrap.paths.control_paths",
            return_value={"/Control": (2, None)},
        ) as control, patch.object(
            registrar,
            "diagnostic_paths",
            return_value={"/Diagnostic": (3, None)},
        ) as diagnostics:
            self.assertEqual(
                registrar.path_map(),
                {
                    "/Measurement": (1, None),
                    "/Control": (2, None),
                    "/Diagnostic": (3, None),
                },
            )

        measurement.assert_called_once()
        control.assert_called_once()
        diagnostics.assert_called_once_with()

    def test_diagnostic_paths_compose_component_defaults_and_state(self) -> None:
        service = mock.Mock(
            _last_health_reason="grid-ok",
            _last_health_code=12,
            _last_auto_state="charging",
            _last_auto_state_code=4,
            _last_status_source=99,
            virtual_mode=0,
        )
        registrar = ServicePathRegistrar(service, script_path="/tmp/service.py", formatters={})
        helper_patches = (
            patch("venus_evcharger.bootstrap.paths.scheduled_diagnostic_defaults", return_value={"/Scheduled": (1, None)}),
            patch("venus_evcharger.bootstrap.paths.backend_diagnostic_defaults", return_value={"/Backend": (2, None)}),
            patch("venus_evcharger.bootstrap.paths.decision_diagnostic_defaults", return_value={"/Decision": (3, None)}),
            patch(
                "venus_evcharger.bootstrap.paths.software_update_diagnostic_defaults",
                return_value={"/Software": (4, None)},
            ),
            patch("venus_evcharger.bootstrap.paths.phase_diagnostic_defaults", return_value={"/Phase": (5, None)}),
            patch("venus_evcharger.bootstrap.paths.age_counter_diagnostic_defaults", return_value={"/Age": (6, None)}),
            patch("venus_evcharger.bootstrap.paths.runtime_timing_diagnostic_defaults", return_value={"/Timing": (7, None)}),
        )
        with helper_patches[0] as scheduled, helper_patches[1] as backend, helper_patches[2] as decision, helper_patches[
            3
        ] as software, helper_patches[4] as phase, helper_patches[5] as age, helper_patches[6] as timing:
            values = registrar.diagnostic_paths()

        self.assertEqual(values["/Auto/Health"], ("grid-ok", None))
        self.assertEqual(values["/Auto/HealthCode"], (12, None))
        self.assertEqual(values["/Auto/State"], ("charging", None))
        self.assertEqual(values["/Auto/StateCode"], (4, None))
        self.assertEqual(values["/Auto/StatusSource"], ("99", None))
        self.assertEqual(values["/Timing"], (7, None))
        scheduled.assert_called_once_with(None)
        for helper in (backend, decision, software, phase):
            helper.assert_called_once_with(service)
        age.assert_called_once_with()
        timing.assert_called_once_with()

    def test_scheduled_diagnostics_use_configured_and_default_values(self) -> None:
        service = mock.Mock(
            virtual_mode=2,
            auto_month_windows={7: ((8, 15), (20, 45))},
            auto_scheduled_enabled_days="Tue,Thu",
            auto_scheduled_night_start_delay_seconds=5400.0,
            auto_scheduled_latest_end_time="07:45",
        )
        registrar = ServicePathRegistrar(service, script_path="/tmp/service.py", formatters={})
        snapshot = object()
        with patch("venus_evcharger.bootstrap.paths.time.time", return_value=1783429200.0), patch(
            "venus_evcharger.bootstrap.paths.scheduled_mode_snapshot",
            return_value=snapshot,
        ) as scheduled, patch(
            "venus_evcharger.bootstrap.paths.scheduled_diagnostic_defaults",
            return_value={"/Scheduled": (snapshot, None)},
        ), patch("venus_evcharger.bootstrap.paths.backend_diagnostic_defaults", return_value={}), patch(
            "venus_evcharger.bootstrap.paths.decision_diagnostic_defaults", return_value={}
        ), patch("venus_evcharger.bootstrap.paths.software_update_diagnostic_defaults", return_value={}), patch(
            "venus_evcharger.bootstrap.paths.phase_diagnostic_defaults", return_value={}
        ), patch("venus_evcharger.bootstrap.paths.age_counter_diagnostic_defaults", return_value={}), patch(
            "venus_evcharger.bootstrap.paths.runtime_timing_diagnostic_defaults", return_value={}
        ):
            self.assertEqual(registrar.diagnostic_paths()["/Scheduled"], (snapshot, None))

        scheduled.assert_called_once()
