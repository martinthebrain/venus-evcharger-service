import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.service_roles_cases_common import _RuntimeService
from tests.wizard_branch_runtime_cases_common import _namespace
from venus_evcharger.bootstrap import wizard as wizard_mod
from venus_evcharger.energy import connectors as connectors_mod
from venus_evcharger.energy import connectors_command as connectors_command_mod
from venus_evcharger.energy import connectors_template as connectors_template_mod


class BranchCoverageNextClusterSixTests(unittest.TestCase):
    def test_connector_timeout_and_text_helpers_cover_fallback_branches(self) -> None:
        runtime = SimpleNamespace(shelly_request_timeout_seconds=3.5)

        self.assertEqual(connectors_command_mod._command_timeout_seconds(runtime, {}, {}), 3.5)
        self.assertEqual(
            connectors_command_mod._command_timeout_seconds(runtime, {"RequestTimeoutSeconds": "0"}, {"TimeoutSeconds": "-1"}),
            3.5,
        )
        self.assertEqual(connectors_template_mod._template_timeout_seconds(runtime, {"RequestTimeoutSeconds": "0"}), 3.5)

        self.assertEqual(connectors_mod._modbus_field_text(SimpleNamespace(), None), "")

        class _FloatClient:
            @staticmethod
            def read_scalar(*_args: object) -> float:
                return 12.5

        field = connectors_mod.ModbusEnergyFieldSettings(
            register_type="holding",
            address=1,
            data_type="uint16",
            scale=1.0,
            word_order="big",
        )
        self.assertEqual(connectors_mod._modbus_field_text(_FloatClient(), field), "12.5")

    def test_runtime_helper_get_system_bus_rejects_direct_dbus_access(self) -> None:
        service = _RuntimeService()
        service._runtime_support_controller = MagicMock()
        service._runtime_support_controller.get_system_bus.return_value = "system-bus"

        with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
            service._get_system_bus()
        service._runtime_support_controller.get_system_bus.assert_not_called()

    def test_resolved_energy_capacity_wh_returns_none_when_prompt_declined(self) -> None:
        namespace = _namespace(energy_recommendation_prefix=["/tmp/huawei-rec"])
        with patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=False):
            self.assertIsNone(wizard_mod._resolved_energy_capacity_wh(namespace, ("/tmp/huawei-rec",)))

    def test_resolved_energy_capacity_wh_returns_none_when_prompt_not_possible(self) -> None:
        self.assertIsNone(wizard_mod._resolved_energy_capacity_wh(_namespace(), tuple()))


if __name__ == "__main__":
    unittest.main()
