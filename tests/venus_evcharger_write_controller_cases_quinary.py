# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *


class TestDbusWriteControllerQuinary(DbusWriteControllerTestBase):
    def test_execute_write_ignores_unknown_paths_and_still_persists_runtime(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _time_now=MagicMock(return_value=42.0),
            _publish_dbus_field=MagicMock(),
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _state_summary=self._state_summary,
        )
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/UnknownPath", 1))

        service._save_runtime_state.assert_called_once()
        service._save_runtime_overrides.assert_called_once()
