# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *


class TestDbusWriteControllerQuinary(DbusWriteControllerTestBase):
    def test_execute_write_rejects_unsupported_paths_without_persistence(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            time_now=MagicMock(return_value=42.0),
            _publish_dbus_field=MagicMock(),
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _state_summary=self._state_summary,
        )
        controller = write_controller(service)

        with self.assertRaisesRegex(
            ValueError,
            "^Unsupported control path '/UnknownPath'\\.$",
        ):
            controller.handle_write("/UnknownPath", 1)

        service._save_runtime_state.assert_not_called()
        service._save_runtime_overrides.assert_not_called()
