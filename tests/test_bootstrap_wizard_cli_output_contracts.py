# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from tests.wizard_branch_coverage_cases_common import _result
from venus_evcharger.bootstrap import wizard_cli_output as output


class BootstrapWizardCliOutputContracts(unittest.TestCase):
    def test_header_lines_are_stable_contracts(self) -> None:
        self.assertEqual(
            output._result_header_lines(_result(live_check={"ok": True})),
            [
                "Config written to: /tmp/config.ini",
                "Imported defaults: none",
                "Selected setup: Recommended: Shelly PM/PM1 Gen4 measures and switches",
                "Selected topology preset: n/a",
                "Responsibilities: one Shelly-compatible device provides both metering and switching; runtime uses the combined backend path",
                "Initial policy mode: Manual charging",
                "Selected charger backend: none",
                "Transport: n/a",
                "Validation: ok",
                "Live connectivity: ok",
            ],
        )
        self.assertEqual(
            output._result_header_lines(
                _result(
                    imported_from="/tmp/import.ini",
                    topology_preset="shelly-meter-goe",
                    charger_backend="goe_charger",
                    transport_kind="tcp",
                    policy_mode="scheduled",
                    live_check={"ok": False},
                )
            ),
            [
                "Config written to: /tmp/config.ini",
                "Imported defaults: /tmp/import.ini",
                "Selected setup: Recommended: Shelly PM/PM1 Gen4 measures and switches",
                "Selected topology preset: Recommended: Shelly meter + native go-e charger",
                "Responsibilities: Shelly measures energy; go-e backend owns charger enable/current/status",
                "Initial policy mode: PV surplus plus scheduled fallback",
                "Selected charger backend: goe_charger",
                "Transport: tcp",
                "Validation: ok",
                "Live connectivity: check reported issues",
            ],
        )

    def test_hardware_flow_lines_cover_fallbacks_and_roles(self) -> None:
        self.assertEqual(
            output._result_configuration_summary_lines(
                _result(role_hosts={"meter": "meter.local", "switch": "switch.local"})
            ),
            [
                "Configuration summary:",
                "  - Target config: /tmp/config.ini",
                "  - Charging policy: Manual charging",
                "  - Hardware flow: meter.local measures energy and switches the relay/contactor.",
            ],
        )
        self.assertEqual(
            output._switch_group_hardware_flow("meter.local", "switch.local", "charger.local"),
            "meter.local measures energy; charger.local controls charging; switch.local switches phases/contactors.",
        )
        self.assertEqual(
            output._switch_group_hardware_flow(None, None, None),
            "the charger backend controls charging; the external switch group switches phases/contactors.",
        )
        self.assertEqual(
            output._cerbo_hardware_flow(None),
            "the configured meter measures energy; the local Cerbo GX relay switches the contactor.",
        )
        self.assertEqual(
            output._hardware_flow_summary(
                _result(
                    topology_preset="shelly-meter-goe-switch-group",
                    role_hosts={"meter": "meter.local", "switch": "switch.local", "charger": "charger.local"},
                )
            ),
            "meter.local measures energy; charger.local controls charging; switch.local switches phases/contactors.",
        )
        self.assertEqual(
            output._hardware_flow_summary(_result(profile="simple_relay", role_hosts={"meter": "meter.local", "switch": "switch.local"})),
            "meter.local measures energy and switches the relay/contactor.",
        )
        self.assertEqual(
            output._hardware_flow_summary(_result(profile="simple_relay", role_hosts={"switch": "switch.local"})),
            "switch.local measures energy and switches the relay/contactor.",
        )
        self.assertEqual(output._simple_relay_hardware_flow("meter.local", None), "meter.local measures energy and switches the relay/contactor.")
        self.assertEqual(output._simple_relay_hardware_flow(None, "switch.local"), "switch.local measures energy and switches the relay/contactor.")
        self.assertEqual(
            output._native_or_generic_hardware_flow(_result(profile="advanced_manual"), None, None),
            "the operator owns the final backend wiring in the generated config.",
        )
        self.assertEqual(
            output._native_or_generic_hardware_flow(
                _result(profile="multi_adapter_topology", topology_preset="shelly-meter-goe"),
                None,
                None,
            ),
            "Shelly measures energy; go-e backend owns charger enable/current/status.",
        )
        self.assertEqual(
            output._native_or_generic_hardware_flow(_result(profile="native_device"), None, "charger.local"),
            "charger.local owns charger control and status where supported.",
        )
        self.assertEqual(
            output._native_or_generic_hardware_flow(_result(profile="native_device"), "meter.local", "charger.local"),
            "meter.local measures energy; charger.local controls charging.",
        )

    def test_warning_classification_and_sort_order_are_explicit(self) -> None:
        for message in ("live connectivity failed", "dbus owner missing", "auth token missing", "password configured"):
            self.assertEqual(output._warning_severity(message), "High")
        for message in ("relay open", "contactor mismatch", "phase switch warning", "auto threshold warning", "surplus low"):
            self.assertEqual(output._warning_severity(message), "Medium")
        self.assertEqual(output._warning_severity("phase mismatch"), "Medium")
        self.assertEqual(output._warning_severity("auto mode note"), "Medium")
        self.assertEqual(output._warning_severity("threshold warning"), "Medium")
        self.assertEqual(output._warning_severity("plain note"), "Low")
        self.assertEqual(output._risk_sort_key(("Unknown", "z")), (3, "z"))
        self.assertEqual(
            sorted(
                [("Low", "c"), ("Unknown", "a"), ("High", "b"), ("Medium", "d")],
                key=output._risk_sort_key,
            ),
            [("High", "b"), ("Medium", "d"), ("Low", "c"), ("Unknown", "a")],
        )
        self.assertEqual(
            output._risk_warning_lines(
                _result(
                    live_check={"ok": False},
                    warnings=("switch warning", "plain note", "auth token missing"),
                    answer_defaults={"password_present": True},
                )
            ),
            [
                "High: Live connectivity check reported issues; fix these before unattended charging.",
                "High: auth token missing",
                "Medium: Authentication credentials are configured; keep generated config and wizard artifacts private.",
                "Medium: switch warning",
                "Low: plain note",
            ],
        )
        self.assertEqual(output._result_warning_lines(_result()), [])
        self.assertEqual(
            output._result_warning_lines(_result(warnings=("plain note",))),
            ["Warnings by risk:", "  - Low: plain note"],
        )
        self.assertEqual(output._derived_risk_warning_items(_result(live_check={"ok": True})), tuple())
        self.assertEqual(
            output._derived_risk_warning_items(
                _result(topology_preset="shelly-meter-goe", answer_defaults={"cerbo_relay_contact_mode": "NC"})
            ),
            tuple(),
        )

    def test_live_check_artifact_and_generated_file_lines_are_exact(self) -> None:
        self.assertIsNone(output._result_live_check_role_line("meter", "ignored"))
        self.assertEqual(output._result_live_check_lines(_result(live_check={"roles": ["ignored"]})), ["Live connectivity by role:"])
        self.assertEqual(output._result_live_check_role_line("meter", {}), "  - meter: unknown (ok)")
        self.assertEqual(output._result_live_check_role_line("meter", {"reason": "sleeping"}), "  - meter: unknown (sleeping)")
        self.assertEqual(
            output._result_live_check_role_line("meter", {"status": "error", "error": "boom", "reason": "sleeping"}),
            "  - meter: error (boom)",
        )
        self.assertEqual(
            output._result_artifact_lines(
                _result(
                    result_path="result.json",
                    audit_path="audit.jsonl",
                    topology_summary_path="topology.txt",
                    inventory_path="inventory.ini",
                )
            ),
            [
                "Wizard result: result.json",
                "Wizard audit: audit.jsonl",
                "Topology summary: topology.txt",
                "Device inventory: inventory.ini",
            ],
        )
        self.assertEqual(
            output._result_generated_file_lines(_result(generated_files=("a.ini", "b.ini"), backup_files=("a.bak",))),
            ["Generated files:", "  - a.ini", "  - b.ini", "Backup files:", "  - a.bak"],
        )

    def test_energy_summary_and_manual_review_text_are_exact(self) -> None:
        self.assertEqual(output._suggested_energy_source_summary({}), "  - unknown: profile=")
        self.assertEqual(
            output._suggested_energy_source_summary(
                {"source_id": "hybrid", "profile": "huawei", "config_path": "source.ini", "host": "", "port": "", "unit_id": ""}
            ),
            "  - hybrid: profile=huawei, config=source.ini",
        )
        self.assertEqual(
            output._suggested_energy_source_summary(
                {"source_id": "hybrid", "profile": "huawei", "unit_id": 3}
            ),
            "  - hybrid: profile=huawei, unit_id=3",
        )
        self.assertEqual(output._suggested_energy_merge_capacity_lines({"capacity_follow_up": "bad"}), [])
        self.assertEqual(
            output._suggested_energy_merge_block_lines("[section]\n\nkey=value"),
            ["  - merge block:", "    [section]", "", "    key=value"],
        )
        rendered = output.result_text(_result(manual_review=("Auth", "Relay")))
        self.assertIn("\nManual review:\n  - Auth\n  - Relay", rendered)


if __name__ == "__main__":
    unittest.main()
