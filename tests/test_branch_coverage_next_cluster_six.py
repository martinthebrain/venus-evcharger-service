import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.wizard_branch_runtime_cases_common import _namespace
from venus_evcharger.bootstrap import wizard_main
from venus_evcharger.energy import connectors as connectors_mod
from venus_evcharger.energy import connectors_command as connectors_command_mod
from venus_evcharger.energy import connectors_modbus as connectors_modbus_mod
from venus_evcharger.energy import connectors_template as connectors_template_mod
from venus_evcharger.service.runtime_facade import ServiceRuntimeFacade


class BranchCoverageNextClusterSixTests(unittest.TestCase):
    def test_connector_timeout_and_text_helpers_cover_fallback_branches(self) -> None:
        runtime = SimpleNamespace(shelly_request_timeout_seconds=3.5)

        self.assertEqual(connectors_command_mod._command_timeout_seconds(runtime, {}, {}), 3.5)
        self.assertEqual(
            connectors_command_mod._command_timeout_seconds(runtime, {"RequestTimeoutSeconds": "0"}, {"TimeoutSeconds": "-1"}),
            3.5,
        )
        self.assertEqual(connectors_template_mod._template_timeout_seconds(runtime, {"RequestTimeoutSeconds": "0"}), 3.5)

        settings = connectors_modbus_mod.ModbusEnergySourceSettings(
            transport_settings=SimpleNamespace(),
            soc_field=None,
            usable_capacity_field=None,
            battery_power_field=None,
            charge_limit_power_field=None,
            discharge_limit_power_field=None,
            ac_power_field=None,
            pv_input_power_field=None,
            grid_interaction_field=None,
            operating_mode_field=None,
            operating_mode_map={"12.5": "support"},
            ac_power_scope_key="",
            pv_input_power_scope_key="",
            grid_interaction_scope_key="",
        )
        self.assertEqual(
            connectors_modbus_mod._modbus_progress_value(
                "operating_mode",
                12.5,
                settings,
            ),
            "support",
        )
        self.assertEqual(
            connectors_modbus_mod._modbus_progress_value("soc", 12.5, settings),
            12.5,
        )

    def test_runtime_facade_has_no_direct_dbus_surface(self) -> None:
        self.assertFalse(hasattr(ServiceRuntimeFacade, "get_system_bus"))

    def test_resolved_energy_capacity_wh_returns_none_when_prompt_declined(self) -> None:
        namespace = _namespace(energy_recommendation_prefix=["/tmp/huawei-rec"])
        with patch("venus_evcharger.bootstrap.wizard_main.prompt_yes_no", return_value=False):
            self.assertIsNone(wizard_main.resolved_energy_capacity_wh(namespace, ("/tmp/huawei-rec",)))

    def test_resolved_energy_capacity_wh_returns_none_when_prompt_not_possible(self) -> None:
        self.assertIsNone(wizard_main.resolved_energy_capacity_wh(_namespace(), tuple()))


if __name__ == "__main__":
    unittest.main()
