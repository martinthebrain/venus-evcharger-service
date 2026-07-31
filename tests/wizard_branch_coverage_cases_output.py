# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_coverage_cases_common import _result, wizard_cli_output


class _WizardBranchCoverageOutputCases:
    def test_result_text_covers_optional_sections_and_live_check_rendering(self) -> None:
        preview_text = wizard_cli_output.result_text(_result(dry_run=True))
        self.assertIn("Config preview for: /tmp/config.ini", preview_text)
        self.assertIn("Configuration summary:", preview_text)
        self.assertIn("Target config: /tmp/config.ini", preview_text)
        self.assertIn("Hardware flow: the configured meter/relay device measures energy and switches", preview_text)
        self.assertIn("Role endpoints:\n  - none", preview_text)
        self.assertIn("Live connectivity: not run", preview_text)
        self.assertIn("Initial policy mode: Manual charging", preview_text)
        self.assertIn("Setup notes:", preview_text)
        self.assertIn("Manual mode follows direct GUI/API start-stop commands", preview_text)
        self.assertIn("Meter/relay setups infer charging", preview_text)
        self.assertIn("Next steps:", preview_text)
        self.assertIn("Post-install checklist:", preview_text)
        self.assertIn("Mode, StartStop, AutoStart, relay state", preview_text)
        self.assertIn("rerun without --dry-run", preview_text)
        self.assertIn("validate-wallbox /tmp/config.ini", preview_text)
        self.assertIn("rerun the wizard with --live-check", preview_text)

        persisted_text = wizard_cli_output.result_text(
            _result(
                imported_from="/tmp/source.ini",
                topology_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                transport_kind="serial_rtu",
                role_hosts={"switch": "switch.local"},
                live_check={
                    "ok": False,
                    "roles": {
                        "switch": {"status": "error", "error": "boom"},
                        "charger": "ignored",
                    },
                },
                backup_files=("/tmp/config.ini.bak",),
                result_path="/tmp/config.ini.wizard-result.json",
                audit_path="/tmp/config.ini.wizard-audit.jsonl",
                topology_summary_path="/tmp/config.ini.wizard-topology.txt",
                warnings=("careful", "dbus owner missing"),
                generated_files=("config.ini", "wizard-switch-group.ini"),
                policy_mode="scheduled",
                answer_defaults={"password_present": True},
            )
        )
        self.assertIn("Imported defaults: /tmp/source.ini", persisted_text)
        self.assertIn("Initial policy mode: PV surplus plus scheduled fallback", persisted_text)
        self.assertIn("Charging policy: PV surplus plus scheduled fallback", persisted_text)
        self.assertIn("Hardware flow: the charger backend controls charging; switch.local switches phases/contactors", persisted_text)
        self.assertIn("Scheduled mode behaves like Auto during the day window", persisted_text)
        self.assertIn("External switch-group adapters own phase/contact switching only", persisted_text)
        self.assertIn("Native charger backends can use charger-side status/control", persisted_text)
        self.assertIn("Backup files:\n  - /tmp/config.ini.bak", persisted_text)
        self.assertIn("Warnings by risk:", persisted_text)
        self.assertIn("High: Live connectivity check reported issues", persisted_text)
        self.assertIn("High: dbus owner missing", persisted_text)
        self.assertIn("Medium: Authentication credentials are configured", persisted_text)
        self.assertIn("Low: careful", persisted_text)
        self.assertIn("Wizard result: /tmp/config.ini.wizard-result.json", persisted_text)
        self.assertIn("Live connectivity: check reported issues", persisted_text)
        self.assertIn("  - switch: error (boom)", persisted_text)
        self.assertIn("Validate generated adapter files individually", persisted_text)
        self.assertIn("Fix the live connectivity issues above", persisted_text)
        self.assertNotIn("charger: ", persisted_text)

        cerbo_text = wizard_cli_output.result_text(
            _result(
                profile="multi_adapter_topology",
                topology_preset="shelly-meter-cerbo-relay",
                role_hosts={"meter": "192.0.2.76"},
                answer_defaults={"cerbo_relay_contact_mode": "NC"},
            )
        )
        self.assertIn("192.0.2.76 measures energy; the local Cerbo GX relay switches", cerbo_text)
        self.assertIn("Medium: Cerbo relay uses NC wiring", cerbo_text)
        self.assertIn("confirm Relay 1/2 and NO/NC wiring", cerbo_text)

        native_text = wizard_cli_output.result_text(
            _result(
                profile="native_device",
                role_hosts={"charger": "charger.local", "meter": "meter.local"},
                charger_backend="modbus_charger",
                warnings=("phase switch warning", "auto threshold warning"),
            )
        )
        self.assertIn("meter.local measures energy; charger.local controls charging", native_text)
        self.assertIn("Medium: phase switch warning", native_text)
        self.assertIn("Medium: auto threshold warning", native_text)

        charger_only_text = wizard_cli_output.result_text(
            _result(
                profile="native_device",
                role_hosts={"charger": "charger.local"},
                charger_backend="goe_charger",
            )
        )
        self.assertIn("charger.local owns charger control and status", charger_only_text)

        generic_text = wizard_cli_output.result_text(_result(profile="advanced_manual", topology_preset=None))
        self.assertIn("the operator owns the final backend wiring", generic_text)

        no_manual_review_text = wizard_cli_output.result_text(_result(manual_review=()))
        self.assertNotIn("Review the Manual review items below", no_manual_review_text)
