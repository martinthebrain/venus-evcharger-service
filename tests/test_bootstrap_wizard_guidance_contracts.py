# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from tests.wizard_branch_runtime_cases_common import _imported_defaults, _namespace
from venus_evcharger.bootstrap import wizard_guidance as guidance


class BootstrapWizardGuidanceContracts(unittest.TestCase):
    def test_topology_prompt_and_role_text_contracts(self) -> None:
        calls: list[tuple[str, tuple[str, ...], dict[str, str] | None, str | None]] = []

        def prompt_choice(
            label: str,
            values: tuple[str, ...],
            labels: dict[str, str] | None,
            default: str | None,
        ) -> str:
            calls.append((label, values, labels, default))
            return "template-stack"

        self.assertEqual(guidance.prompt_topology_preset(prompt_choice, "shelly-meter-goe"), "template-stack")
        self.assertEqual(calls[0][0], "Choose the topology preset:")
        self.assertIn("template-stack", calls[0][1])
        self.assertIn("template adapters keep", (calls[0][2] or {})["template-stack"])
        self.assertEqual(calls[0][3], "shelly-meter-goe")

        self.assertEqual(guidance.relevant_role_hosts("simple_relay", None), ())
        self.assertEqual(guidance.relevant_role_hosts("native_device", None), ("charger",))
        self.assertEqual(guidance.relevant_role_hosts("multi_adapter_topology", "template-stack"), ("meter", "switch", "charger"))
        self.assertEqual(guidance.relevant_role_hosts("multi_adapter_topology", "unknown"), ())
        self.assertEqual(guidance.relevant_role_hosts("multi_adapter_topology", None), ())
        self.assertEqual(guidance.role_prompt_intro("native_device", None), "This setup only needs the charger endpoint.")
        self.assertEqual(
            guidance.role_prompt_intro("multi_adapter_topology", None),
            "This topology uses separate adapter roles. We will ask for each role endpoint separately.",
        )
        self.assertEqual(
            guidance.role_prompt_intro("multi_adapter_topology", "template-stack"),
            "This topology keeps meter, switch, and charger as separate adapter roles backed by template adapters.",
        )
        self.assertEqual(
            guidance.role_prompt_intro("multi_adapter_topology", "unknown"),
            "This topology uses separate adapter roles. We will ask for each role endpoint separately.",
        )
        self.assertIsNone(guidance.role_prompt_intro("advanced_manual", None))
        self.assertEqual(guidance.role_prompt_label("meter", None), "Meter endpoint (host or full BaseUrl)")
        self.assertEqual(guidance.role_prompt_label("switch", "template-stack"), "Switch endpoint (host or full BaseUrl)")
        self.assertEqual(
            guidance.role_prompt_label("switch", "goe-external-switch-group"),
            "External phase-switch endpoint (host or full BaseUrl)",
        )
        self.assertEqual(guidance.role_prompt_label("charger", None), "Charger endpoint (host or full BaseUrl)")

    def test_role_defaults_prompting_and_primary_host_contracts(self) -> None:
        imported = _imported_defaults(meter_host_input="imported-meter", charger_host_input="imported-charger", host_input="imported-primary")
        namespace = _namespace(switch_host="explicit-switch")
        self.assertEqual(
            guidance.role_host_defaults(namespace, imported, "multi_adapter_topology", "template-stack", "shared.local"),
            ("imported-meter", "explicit-switch", "imported-charger"),
        )
        self.assertEqual(
            guidance.role_host_defaults(_namespace(), _imported_defaults(), "multi_adapter_topology", "template-stack", "shared.local"),
            ("shared.local", "shared.local", "shared.local"),
        )
        self.assertEqual(
            guidance.role_host_defaults(_namespace(), _imported_defaults(), "native_device", None, "shared.local"),
            (None, None, "shared.local"),
        )
        self.assertEqual(
            guidance.resolved_primary_host(_namespace(host="cli-host"), imported, None, None, None),
            "cli-host",
        )
        self.assertEqual(
            guidance.resolved_primary_host(_namespace(), _imported_defaults(host_input=None), None, "switch.local", None),
            "switch.local",
        )
        self.assertEqual(
            guidance.resolved_primary_host(_namespace(), _imported_defaults(host_input=None), None, None, None),
            "192.168.1.50",
        )

        prompts: list[tuple[str, str]] = []

        def prompt_text(label: str, default: str) -> str:
            prompts.append((label, default))
            return f"prompted-{default}"

        self.assertEqual(
            guidance.prompt_role_hosts(
                _namespace(switch_host="explicit-switch"),
                _imported_defaults(),
                "multi_adapter_topology",
                "template-stack",
                "shared.local",
                prompt_text=prompt_text,
            ),
            ("prompted-shared.local", "explicit-switch", "prompted-shared.local"),
        )
        self.assertEqual(
            prompts,
            [
                ("Charger endpoint (host or full BaseUrl)", "shared.local"),
                ("Meter endpoint (host or full BaseUrl)", "shared.local"),
            ],
        )
        prompts.clear()
        self.assertEqual(
            guidance.prompt_role_hosts(
                _namespace(),
                _imported_defaults(),
                "multi_adapter_topology",
                "goe-external-switch-group",
                "shared.local",
                prompt_text=prompt_text,
            ),
            (None, "prompted-shared.local", "prompted-shared.local"),
        )
        self.assertEqual(
            prompts,
            [
                ("Charger endpoint (host or full BaseUrl)", "shared.local"),
                ("External phase-switch endpoint (host or full BaseUrl)", "shared.local"),
            ],
        )
        self.assertEqual(
            guidance.prompt_role_hosts(
                _namespace(),
                _imported_defaults(),
                "simple_relay",
                None,
                "shared.local",
                prompt_text=prompt_text,
            ),
            (None, None, None),
        )

    def test_backend_resolution_and_probe_roles_contracts(self) -> None:
        self.assertEqual(guidance.default_backend("native_device", None), "goe_charger")
        self.assertEqual(guidance.default_backend("hybrid_topology", None), "simpleevse_charger")
        self.assertIsNone(guidance.default_backend("advanced_manual", None))
        self.assertEqual(guidance.default_backend("native_device", _imported_defaults(charger_backend="template_charger")), "template_charger")
        self.assertEqual(guidance.apply_topology_preset_backend(None, "template_charger"), "template_charger")
        self.assertEqual(guidance.apply_topology_preset_backend("unknown", "template_charger"), "template_charger")
        self.assertEqual(guidance.apply_topology_preset_backend("shelly-meter-goe", "template_charger"), "goe_charger")
        self.assertEqual(
            guidance.apply_topology_preset_backend("shelly-meter-goe", "goe_charger", "cfos-power-brain-modbus"),
            "modbus_charger",
        )
        self.assertEqual(
            guidance.apply_topology_preset_backend("shelly-meter-modbus-charger", "template_charger", "cfos-power-brain-modbus"),
            "modbus_charger",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported topology backend: mystery"):
            guidance._known_charger_backend("mystery")
        self.assertEqual(guidance.probe_roles(_namespace(probe_roles=["meter", "charger"])), ("meter", "charger"))
        self.assertIsNone(guidance.probe_roles(_namespace(probe_roles=[])))
        self.assertIsNone(guidance.probe_roles(_namespace(probe_roles=None)))

    def test_compatibility_warnings_contracts(self) -> None:
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="native_device",
                topology_preset=None,
                charger_backend="template_charger",
                primary_host_input="primary.local",
                role_hosts={"charger": "charger.local"},
                transport_kind="serial_rtu",
                transport_host="primary.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            ),
            (),
        )
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="multi_adapter_topology",
                topology_preset=None,
                charger_backend="modbus_charger",
                primary_host_input="shared.local",
                role_hosts={"switch": "shared.local"},
                transport_kind="serial_rtu",
                transport_host="shared.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            ),
            (),
        )
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="multi_adapter_topology",
                topology_preset="template-stack",
                charger_backend="goe_charger",
                primary_host_input="shared.local",
                role_hosts={"switch": "shared.local", "charger": "charger.local"},
                transport_kind="serial_rtu",
                transport_host="shared.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            ),
            (),
        )
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="multi_adapter_topology",
                topology_preset="goe-external-switch-group",
                charger_backend="template_charger",
                primary_host_input="shared.local",
                role_hosts={"meter": "shared.local"},
                transport_kind="tcp",
                transport_host="shared.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            ),
            (),
        )
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="multi_adapter_topology",
                topology_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                primary_host_input="shared.local",
                role_hosts={"meter": "shared.local", "switch": "shared.local"},
                transport_kind="serial_rtu",
                transport_host="other.local",
                switch_group_supported_phase_selections="P1,P1_P2_P3",
                charger_preset="openwb-modbus-secondary",
            ),
            (
                "Multiple topology roles resolve to the same shared endpoint; verify that this combined-host layout is intentional.",
                "This switch_group preset is using the shared primary endpoint for the external phase switch; verify that the switch adapter is really colocated there.",
                "The go-e preset fell back to the shared primary endpoint; set an explicit charger endpoint if the charger lives elsewhere.",
                "The openWB Modbus preset expects the charger in secondary Modbus mode. If you enable the openWB heartbeat, keep this service polling continuously so the heartbeat does not expire.",
                "The external phase switch is configured for 1P -> 3P switching only; make sure a 2-phase step is intentionally unavailable.",
            ),
        )
        self.assertEqual(
            guidance._switch_group_warning("custom-switch-group", ["switch"]),
            "This switch_group preset is using the shared primary endpoint for the external phase switch; verify that the switch adapter is really colocated there.",
        )
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="native_device",
                topology_preset=None,
                charger_backend="modbus_charger",
                primary_host_input="http://charger.local/status",
                role_hosts={"charger": "charger.local"},
                transport_kind="tcp",
                transport_host="charger.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
                charger_preset="cfos-power-brain-modbus",
            ),
            (
                "The Modbus TCP charger currently uses the primary service host as transport host; confirm charger address and unit id.",
                "The cFos preset only writes charging_enable, charging_cur_limit, and relay_select regularly; avoid adding periodic writes to other cFos registers because they persist to flash.",
            ),
        )
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="native_device",
                topology_preset=None,
                charger_backend="template_charger",
                primary_host_input="charger.local",
                role_hosts={},
                transport_kind="tcp",
                transport_host="charger.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            ),
            (),
        )
        self.assertEqual(
            guidance.compatibility_warnings(
                profile="native_device",
                topology_preset=None,
                charger_backend="modbus_charger",
                primary_host_input="charger.local",
                role_hosts={},
                transport_kind="serial_rtu",
                transport_host="charger.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
