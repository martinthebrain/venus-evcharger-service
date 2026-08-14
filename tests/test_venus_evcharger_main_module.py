# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.modules["vedbus"] = MagicMock()
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()

import venus_evcharger_service
from venus_evcharger.service.state_facade import ServiceStateFacade
from venus_evcharger_service import ShellyWallboxService


class TestShellyWallboxMainModule(unittest.TestCase):
    def test_service_clock_uses_wall_clock(self) -> None:
        with patch("venus_evcharger_service.time.time", return_value=123.0):
            self.assertEqual(ShellyWallboxService.time_now(), 123.0)

    def test_init_composes_explicit_owner_and_facades(self) -> None:
        owner = MagicMock()
        runtime = MagicMock()
        state = MagicMock()
        update = MagicMock()
        control = MagicMock()
        auto = MagicMock()
        with (
            patch("venus_evcharger_service.ServiceControllerOwner", return_value=owner) as owner_factory,
            patch("venus_evcharger_service.ServiceRuntimeFacade", return_value=runtime) as runtime_factory,
            patch("venus_evcharger_service.ServiceStateFacade", return_value=state) as state_factory,
            patch("venus_evcharger_service.ServiceUpdateFacade", return_value=update) as update_factory,
            patch("venus_evcharger_service.ServiceControlFacade", return_value=control) as control_factory,
            patch("venus_evcharger_service.ServiceAutoFacade", return_value=auto) as auto_factory,
        ):
            state_factory.config_path.return_value = "/config.ini"
            service = ShellyWallboxService()

        owner_factory.assert_called_once()
        owner_service, functions = owner_factory.call_args.args
        self.assertIs(owner_service, service)
        self.assertEqual(functions.config_path, "/config.ini")
        self.assertTrue(
            functions.auto_input_helper_path.endswith(
                "deploy/venus/bin/venus-evcharger-auto-input-helper",
            )
        )
        runtime_factory.assert_called_once_with(owner)
        state_factory.assert_called_once_with(owner, runtime)
        update_factory.assert_called_once_with(owner)
        control_factory.assert_called_once_with(service)
        auto_owner, event_publisher = auto_factory.call_args.args
        self.assertIs(auto_owner, owner)
        self.assertIs(event_publisher, control.publish_command_event)
        owner.bootstrap.initialize_service.assert_called_once_with()

    def test_main_delegates_with_state_facade_config_path(self) -> None:
        with (
            patch.object(ServiceStateFacade, "config_path", return_value="/config.ini") as config_path,
            patch("venus_evcharger_service.run_service_main") as run_service_main,
        ):
            venus_evcharger_service.main()
        config_path.assert_called_once_with()
        run_service_main.assert_called_once_with(
            ShellyWallboxService,
            "/config.ini",
            venus_evcharger_service.gobject,
        )
