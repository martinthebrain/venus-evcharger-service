# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
from unittest.mock import patch

from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.controllers.state_runtime_overrides import RuntimeOverrideStore
from tests.venus_evcharger_state_controller_support import (
    STATE_RUNTIME_LOG_WARNING,
    ServiceStateControllerTestBase,
)
from tests.venus_evcharger_test_fixtures import make_runtime_state_service


class TestServiceStateControllerQuinary(ServiceStateControllerTestBase):
    def test_load_runtime_state_handles_missing_path_invalid_json_and_load_config_errors(self) -> None:
        service = make_runtime_state_service(
            virtual_mode=0,
            virtual_enable=0,
            virtual_startstop=1,
        )
        controller = ServiceStateController(service, self._normalize_mode)
        controller.load_runtime_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            service.runtime_state_path = path
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{bad json")
            with patch(STATE_RUNTIME_LOG_WARNING) as warning_mock:
                controller.load_runtime_state()
            warning_mock.assert_called_once()

        parser = tempfile.TemporaryDirectory()
        self.addCleanup(parser.cleanup)
        missing_config_controller = ServiceStateController(service, self._normalize_mode)
        with patch.object(ServiceStateController, "config_path", return_value=os.path.join(parser.name, "missing.ini")):
            with self.assertRaises(ValueError):
                missing_config_controller.load_config()

    def test_runtime_restore_helpers_cover_non_dict_fault_and_mismatch_maps(self) -> None:
        service = make_runtime_state_service()
        controller = self.runtime_restorer(service)

        self.assertEqual(controller._normalized_phase_switch_mismatch_counts([], "P1"), {})
        self.assertEqual(controller._normalized_contactor_fault_counts([]), {})

    def test_runtime_override_pending_payload_requires_dict_values_and_text(self) -> None:
        service = make_runtime_state_service(
            runtime_overrides_path="/tmp/runtime.ini",
            _runtime_overrides_pending_serialized='{"Mode":"1"}',
            _runtime_overrides_pending_values=[],
            _runtime_overrides_pending_text=None,
        )
        controller = RuntimeOverrideStore(service, RuntimeStateNormalizer())

        self.assertIsNone(controller._pending_runtime_overrides_payload(service, "/tmp/runtime.ini"))
