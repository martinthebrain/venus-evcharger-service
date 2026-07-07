# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from tests.wizard_branch_coverage_cases_common import _result
from venus_evcharger.bootstrap import wizard_cli_output_next as output_next


class BootstrapWizardCliOutputNextContracts(unittest.TestCase):
    def test_setup_notes_are_exact_for_key_topologies(self) -> None:
        self.assertEqual(
            output_next.result_setup_note_lines(_result()),
            [
                "Setup notes:",
                "  - Manual mode follows direct GUI/API start-stop commands; surplus thresholds are not used.",
                "  - Meter/relay setups infer charging from power and energy deltas, not from vehicle communication.",
            ],
        )
        self.assertEqual(
            output_next.result_setup_note_lines(
                _result(profile="multi_adapter_topology", topology_preset="shelly-meter-cerbo-relay")
            ),
            [
                "Setup notes:",
                "  - Manual mode follows direct GUI/API start-stop commands; surplus thresholds are not used.",
                "  - Cerbo GX relay switching sets the Venus OS relay function to Manual before changing relay state.",
                "  - Meter/relay setups infer charging from power and energy deltas, not from vehicle communication.",
            ],
        )
        self.assertEqual(
            output_next.result_setup_note_lines(
                _result(
                    profile="multi_adapter_topology",
                    topology_preset="goe-external-switch-group",
                    charger_backend="goe_charger",
                    policy_mode="scheduled",
                )
            ),
            [
                "Setup notes:",
                "  - Scheduled mode behaves like Auto during the day window, then uses the configured night fallback after the latest end time.",
                "  - External switch-group adapters own phase/contact switching only; the charger backend still owns charger control.",
                "  - Native charger backends can use charger-side status/control where the device supports it.",
            ],
        )

    def test_next_step_lines_are_exact_for_live_check_states(self) -> None:
        self.assertEqual(
            output_next.result_next_step_lines(_result(live_check=None, manual_review=(), dry_run=False, generated_files=("config.ini",))),
            [
                "Next steps:",
                "  - Validate the full setup: python3 -m venus_evcharger.backend.probe validate-wallbox /tmp/config.ini",
                "  - Optional: rerun the wizard with --live-check once the devices are reachable.",
            ],
        )
        self.assertEqual(
            output_next.result_next_step_lines(
                _result(dry_run=True, manual_review=("Auth",), live_check={"ok": False}, generated_files=("config.ini", "adapter.ini"))
            ),
            [
                "Next steps:",
                "  - Review this preview, then rerun without --dry-run to write the files.",
                "  - Review the Manual review items below before enabling unattended charging.",
                "  - Validate the full setup: python3 -m venus_evcharger.backend.probe validate-wallbox /tmp/config.ini",
                "  - Validate generated adapter files individually with: python3 -m venus_evcharger.backend.probe validate <adapter.ini>",
                "  - Fix the live connectivity issues above, then rerun with --live-check.",
            ],
        )
        self.assertEqual(
            output_next.result_next_step_lines(_result(live_check={"ok": True}, manual_review=(), generated_files=("config.ini", "adapter.ini"))),
            [
                "Next steps:",
                "  - Validate the full setup: python3 -m venus_evcharger.backend.probe validate-wallbox /tmp/config.ini",
                "  - Validate generated adapter files individually with: python3 -m venus_evcharger.backend.probe validate <adapter.ini>",
            ],
        )

    def test_post_install_checklist_and_file_helpers_are_exact(self) -> None:
        self.assertEqual(
            output_next.result_post_install_checklist_lines(_result()),
            [
                "Post-install checklist:",
                "  - In the Venus GUI, confirm Mode, StartStop, AutoStart, relay state, and measured charging power.",
                "  - Start with a safe manual test before relying on unattended Auto or Scheduled charging.",
                "  - For meter/relay setups, confirm session energy starts at zero after unplug/replug.",
            ],
        )
        self.assertEqual(
            output_next.result_post_install_checklist_lines(
                _result(profile="multi_adapter_topology", topology_preset="shelly-meter-cerbo-relay")
            ),
            [
                "Post-install checklist:",
                "  - In the Venus GUI, confirm Mode, StartStop, AutoStart, relay state, and measured charging power.",
                "  - Start with a safe manual test before relying on unattended Auto or Scheduled charging.",
                "  - For Cerbo relay setups, confirm Relay 1/2 and NO/NC wiring match the generated config.",
                "  - For meter/relay setups, confirm session energy starts at zero after unplug/replug.",
            ],
        )
        self.assertTrue(output_next._has_adapter_files(_result(generated_files=("config.ini", "adapter.ini"))))
        self.assertFalse(output_next._has_adapter_files(_result(generated_files=("config.ini",))))
        self.assertTrue(output_next._is_meter_relay_setup("simple_relay", None))
        self.assertFalse(output_next._is_meter_relay_setup("native_device", None))
        self.assertTrue(output_next._is_meter_relay_setup("multi_adapter_topology", "shelly-meter-goe"))
        self.assertFalse(output_next._is_meter_relay_setup("multi_adapter_topology", "goe-external-switch-group"))
        for backend in ("goe_charger", "modbus_charger", "simpleevse_charger", "smartevse_charger"):
            self.assertTrue(output_next._has_native_charger_backend(backend))
        self.assertFalse(output_next._has_native_charger_backend("template_charger"))
        self.assertFalse(output_next._has_native_charger_backend(None))
        self.assertEqual(output_next._config_filename("/tmp/a/config.ini"), "config.ini")
        self.assertEqual(output_next._config_filename("/tmp/a/"), "a")


if __name__ == "__main__":
    unittest.main()
