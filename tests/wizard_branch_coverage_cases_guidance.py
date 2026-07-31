# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import patch

from tests.wizard_branch_coverage_cases_common import (
    _imported_defaults,
    _namespace,
    apply_charger_preset_backend,
    apply_topology_preset_backend,
    charger_preset_backend,
    compatibility_warnings,
    default_backend,
    default_transport_kind,
    host_from_input,
    preset_specific_defaults,
    preset_transport_port,
    preset_transport_unit_id,
    probe_roles,
    prompt_preset_specific_defaults,
    prompt_role_hosts,
    prompt_transport_inputs,
    relevant_charger_presets,
    relevant_role_hosts,
    resolved_primary_host,
    role_prompt_intro,
    role_prompt_label,
    transport_summary,
)


class _WizardBranchCoverageGuidanceCases:
    def test_wizard_support_validates_hosts_and_transport_summary(self) -> None:
        self.assertEqual(host_from_input("http://charger.local/status"), "charger.local")
        self.assertEqual(__import__("tests.wizard_branch_coverage_cases_common", fromlist=["base_url_from_input"]).base_url_from_input("charger.local/"), "http://charger.local")
        self.assertEqual(default_transport_kind("modbus_charger"), "tcp")
        self.assertEqual(transport_summary("modbus_charger", "tcp"), "tcp")
        self.assertIsNone(transport_summary("goe_charger", "serial_rtu"))
        with self.assertRaisesRegex(ValueError, "Invalid host input"):
            host_from_input("http://")
        with self.assertRaisesRegex(ValueError, "Host must not be empty"):
            host_from_input("   ")
        with self.assertRaisesRegex(ValueError, "Host must not be empty"):
            __import__("tests.wizard_branch_coverage_cases_common", fromlist=["base_url_from_input"]).base_url_from_input("   ")

    def test_wizard_guidance_helpers_cover_role_defaults_and_warnings(self) -> None:
        imported = _imported_defaults(charger_backend="template_charger")
        namespace = _namespace(switch_host="switch.explicit")
        prompted: list[tuple[str, str]] = []

        def prompt_text(label: str, default: str) -> str:
            prompted.append((label, default))
            return f"prompted-{default}"

        meter_host, switch_host, charger_host = prompt_role_hosts(
            namespace,
            imported,
            "multi_adapter_topology",
            "template-stack",
            "shared.local",
            prompt_text=prompt_text,
        )
        self.assertEqual(relevant_role_hosts("advanced_manual", None), ())
        self.assertEqual(relevant_role_hosts("hybrid_topology", None), ("charger", "switch"))
        self.assertIn("separate adapter roles", role_prompt_intro("multi_adapter_topology", "template-stack") or "")
        self.assertEqual(role_prompt_intro("native_device", None), "This setup only needs the charger endpoint.")
        self.assertEqual(role_prompt_label("switch", "template-stack"), "Switch endpoint (host or full BaseUrl)")
        self.assertEqual(role_prompt_label("switch", "goe-external-switch-group"), "External phase-switch endpoint (host or full BaseUrl)")
        self.assertEqual((meter_host, switch_host, charger_host), ("prompted-shared.local", "switch.explicit", "prompted-shared.local"))
        self.assertEqual(resolved_primary_host(_namespace(), _imported_defaults(), None, None, None), "192.168.1.50")
        self.assertEqual(default_backend("native_device", imported), "template_charger")
        self.assertEqual(apply_topology_preset_backend("shelly-meter-goe", "template_charger"), "goe_charger")
        self.assertEqual(apply_topology_preset_backend("shelly-meter-modbus-charger", "template_charger", "abb-terra-ac-modbus"), "modbus_charger")
        with patch.dict("venus_evcharger.bootstrap.wizard_guidance.TOPOLOGY_PRESET_BACKENDS", {"bad-backend": "mystery"}):
            with self.assertRaisesRegex(ValueError, "Unsupported topology backend"):
                apply_topology_preset_backend("bad-backend", None)
        self.assertEqual(charger_preset_backend("abb-terra-ac-modbus"), "modbus_charger")
        self.assertEqual(apply_charger_preset_backend("cfos-power-brain-modbus", "template_charger"), "modbus_charger")
        self.assertEqual(relevant_charger_presets("modbus_charger"), ("abb-terra-ac-modbus", "cfos-power-brain-modbus", "openwb-modbus-secondary"))
        self.assertEqual(relevant_charger_presets("goe_charger"), ())
        self.assertEqual(
            compatibility_warnings(
                profile="multi_adapter_topology",
                topology_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                primary_host_input="shared.local",
                role_hosts={"switch": "shared.local"},
                transport_kind="serial_rtu",
                transport_host="shared.local",
                switch_group_supported_phase_selections="P1,P1_P2_P3",
                charger_preset="cfos-power-brain-modbus",
            ),
            (
                "This switch_group preset is using the shared primary endpoint for the external phase switch; verify that the switch adapter is really colocated there.",
                "The go-e preset fell back to the shared primary endpoint; set an explicit charger endpoint if the charger lives elsewhere.",
                "The cFos preset only writes charging_enable, charging_cur_limit, and relay_select regularly; avoid adding periodic writes to other cFos registers because they persist to flash.",
                "The external phase switch is configured for 1P -> 3P switching only; make sure a 2-phase step is intentionally unavailable.",
            ),
        )
        self.assertIn(
            "Multiple topology roles resolve to the same shared endpoint",
            compatibility_warnings(
                profile="multi_adapter_topology",
                topology_preset="template-stack",
                charger_backend="modbus_charger",
                primary_host_input="shared.local",
                role_hosts={"meter": "shared.local", "switch": "shared.local"},
                transport_kind="tcp",
                transport_host="shared.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            )[0],
        )
        self.assertEqual(probe_roles(_namespace(probe_roles=["meter", "charger"])), ("meter", "charger"))
        self.assertIsNone(probe_roles(_namespace()))

    def test_transport_guidance_covers_serial_tcp_and_prompted_defaults(self) -> None:
        imported = _imported_defaults(transport_kind="tcp", transport_host="198.51.100.2", transport_port=1502, transport_unit_id=7)

        tcp_transport = prompt_transport_inputs(
            "modbus_charger",
            "abb-terra-ac-modbus",
            "198.51.100.1",
            imported,
            prompt_choice=lambda *_args: "tcp",
            prompt_text=lambda label, default: {"Modbus TCP host": "198.51.100.8", "Modbus TCP port": "2502", "Modbus unit id": "9"}[label],
        )
        serial_transport = prompt_transport_inputs(
            "simpleevse_charger",
            None,
            "serial.local",
            _imported_defaults(),
            prompt_choice=lambda *_args: "serial_rtu",
            prompt_text=lambda label, default: {"Serial device": "/dev/ttyUSB9", "Modbus unit id": "4"}[label],
        )
        self.assertEqual(tcp_transport, ("tcp", "198.51.100.8", 2502, "/dev/ttyUSB0", 9))
        self.assertEqual(serial_transport, ("serial_rtu", "serial.local", 502, "/dev/ttyUSB9", 4))
        self.assertEqual(preset_transport_port("cfos-power-brain-modbus", "tcp"), 4701)
        self.assertEqual(preset_transport_port("openwb-modbus-secondary", "tcp"), 1502)
        self.assertIsNone(preset_transport_port("cfos-power-brain-modbus", "serial_rtu"))
        self.assertEqual(preset_transport_unit_id("abb-terra-ac-modbus"), 1)

        timeout, phase_layout = preset_specific_defaults(_namespace(), _imported_defaults(), backend="template_charger", topology_preset=None)
        self.assertIsNone(timeout)
        self.assertEqual(phase_layout, "P1,P1_P2,P1_P2_P3")

        prompted_timeout, prompted_phase_layout = prompt_preset_specific_defaults(
            _namespace(),
            _imported_defaults(request_timeout_seconds=5.0),
            profile="multi_adapter_topology",
            backend="goe_charger",
            topology_preset="goe-external-switch-group",
            prompt_choice=lambda *_args: "P1,P1_P2_P3",
            prompt_text=lambda label, default: "6.5" if "timeout" in label else default,
        )
        self.assertEqual(prompted_timeout, 6.5)
        self.assertEqual(prompted_phase_layout, "P1,P1_P2_P3")
