# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from tests.wizard_branch_runtime_cases_common import _namespace, _result
from venus_evcharger.bootstrap import wizard_main


class BootstrapWizardMainTests(unittest.TestCase):
    def test_resolve_live_check_contracts(self) -> None:
        with patch("venus_evcharger.bootstrap.wizard_main.prompt_yes_no") as prompt:
            self.assertTrue(wizard_main.resolve_live_check(_namespace(live_check=True)))
            self.assertTrue(wizard_main.resolve_live_check(_namespace(probe_roles=("charger",))))
            prompt.assert_not_called()

        with patch("venus_evcharger.bootstrap.wizard_main.prompt_yes_no") as prompt:
            self.assertFalse(wizard_main.resolve_live_check(_namespace(non_interactive=True)))
            self.assertFalse(wizard_main.resolve_live_check(_namespace(yes=True)))
            prompt.assert_not_called()

        with patch("venus_evcharger.bootstrap.wizard_main.prompt_yes_no", return_value=True) as prompt:
            self.assertTrue(wizard_main.resolve_live_check(_namespace()))
            prompt.assert_called_once_with("Run optional live connectivity checks now?", False)

        incomplete_namespace = argparse.Namespace(live_check=False, non_interactive=False, yes=False)
        with patch("venus_evcharger.bootstrap.wizard_main.prompt_yes_no", return_value=False) as prompt:
            self.assertFalse(wizard_main.resolve_live_check(incomplete_namespace))
            prompt.assert_called_once_with("Run optional live connectivity checks now?", False)

    def test_run_wizard_orchestrates_preview_confirmation_and_persisted_write(self) -> None:
        answers = object()
        preview = _result()
        persisted = _result()
        namespace = _namespace(
            config_path="/tmp/target.ini",
            template_path="/tmp/template.ini",
            apply_energy_merge=True,
            energy_recommendation_prefix=("/energy",),
            huawei_recommendation_prefix=("/huawei",),
        )
        with (
            patch("venus_evcharger.bootstrap.wizard_main.build_answers", return_value=(answers, SimpleNamespace(imported_from="seed.ini"))),
            patch("venus_evcharger.bootstrap.wizard_main.resolve_live_check", return_value=True),
            patch("venus_evcharger.bootstrap.wizard_main.probe_roles", return_value=("charger",)),
            patch("venus_evcharger.bootstrap.wizard_main.merged_recommendation_prefixes", return_value=("merged",)) as merged,
            patch("venus_evcharger.bootstrap.wizard_main.resolved_energy_capacity_wh", return_value=15360.0) as capacity,
            patch("venus_evcharger.bootstrap.wizard_main.resolved_energy_capacity_overrides", return_value={"hybrid": 7680.0}) as overrides,
            patch("venus_evcharger.bootstrap.wizard_main.configure_wallbox", side_effect=[preview, persisted]) as configure,
            patch("venus_evcharger.bootstrap.wizard_main._existing_output_paths", return_value=("existing.ini",)) as existing,
            patch("venus_evcharger.bootstrap.wizard_main._confirm_write") as confirm,
        ):
            result = wizard_main.run_wizard(namespace)

        self.assertIs(result, persisted)
        merged.assert_called_once_with(("/energy",), ("/huawei",))
        capacity.assert_called_once_with(namespace, ("merged",))
        overrides.assert_called_once_with(namespace)
        existing.assert_called_once_with(Path("/tmp/target.ini"), preview.generated_files)
        confirm.assert_called_once_with(namespace, preview, ("existing.ini",))
        self.assertEqual(
            configure.call_args_list,
            [
                call(
                    answers,
                    config_path=Path("/tmp/target.ini"),
                    template_path=Path("/tmp/template.ini"),
                    dry_run=True,
                    live_check=True,
                    selected_probe_roles=("charger",),
                    imported_from="seed.ini",
                    energy_recommendation_prefix=("merged",),
                    huawei_recommendation_prefix=("merged",),
                    apply_suggested_energy_merge=True,
                    suggested_energy_capacity_wh=15360.0,
                    suggested_energy_capacity_overrides={"hybrid": 7680.0},
                ),
                call(
                    answers,
                    config_path=Path("/tmp/target.ini"),
                    template_path=Path("/tmp/template.ini"),
                    dry_run=False,
                    live_check=True,
                    selected_probe_roles=("charger",),
                    imported_from="seed.ini",
                    energy_recommendation_prefix=("merged",),
                    huawei_recommendation_prefix=("merged",),
                    apply_suggested_energy_merge=True,
                    suggested_energy_capacity_wh=15360.0,
                    suggested_energy_capacity_overrides={"hybrid": 7680.0},
                ),
            ],
        )

    def test_run_wizard_returns_preview_when_dry_run_is_requested(self) -> None:
        answers = object()
        preview = _result()
        namespace = _namespace(dry_run=True, config_path="/tmp/target.ini", template_path="/tmp/template.ini")
        with (
            patch("venus_evcharger.bootstrap.wizard_main.build_answers", return_value=(answers, None)),
            patch("venus_evcharger.bootstrap.wizard_main.resolve_live_check", return_value=False),
            patch("venus_evcharger.bootstrap.wizard_main.probe_roles", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_main.merged_recommendation_prefixes", return_value=tuple()),
            patch("venus_evcharger.bootstrap.wizard_main.resolved_energy_capacity_wh", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_main.resolved_energy_capacity_overrides", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_main.configure_wallbox", return_value=preview) as configure,
            patch("venus_evcharger.bootstrap.wizard_main._existing_output_paths", return_value=tuple()),
            patch("venus_evcharger.bootstrap.wizard_main._confirm_write"),
        ):
            self.assertIs(wizard_main.run_wizard(namespace), preview)

        configure.assert_called_once()
        self.assertFalse(configure.call_args.kwargs["apply_suggested_energy_merge"])

    def test_run_wizard_defaults_missing_apply_energy_merge_to_false_for_write(self) -> None:
        answers = object()
        preview = _result()
        persisted = _result()
        namespace = _namespace(config_path="/tmp/target.ini", template_path="/tmp/template.ini")
        self.assertFalse(hasattr(namespace, "apply_energy_merge"))
        with (
            patch("venus_evcharger.bootstrap.wizard_main.build_answers", return_value=(answers, None)),
            patch("venus_evcharger.bootstrap.wizard_main.resolve_live_check", return_value=False),
            patch("venus_evcharger.bootstrap.wizard_main.probe_roles", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_main.merged_recommendation_prefixes", return_value=tuple()),
            patch("venus_evcharger.bootstrap.wizard_main.resolved_energy_capacity_wh", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_main.resolved_energy_capacity_overrides", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_main.configure_wallbox", side_effect=[preview, persisted]) as configure,
            patch("venus_evcharger.bootstrap.wizard_main._existing_output_paths", return_value=tuple()),
            patch("venus_evcharger.bootstrap.wizard_main._confirm_write"),
        ):
            self.assertIs(wizard_main.run_wizard(namespace), persisted)

        self.assertEqual(configure.call_count, 2)
        self.assertIs(configure.call_args_list[0].kwargs["apply_suggested_energy_merge"], False)
        self.assertIs(configure.call_args_list[1].kwargs["apply_suggested_energy_merge"], False)

    def test_main_dispatches_inventory_success_value_error_and_wizard_success(self) -> None:
        parser = MagicMock()
        inventory_namespace = argparse.Namespace(inventory_action="show")
        parser.parse_args.return_value = inventory_namespace
        with (
            patch("venus_evcharger.bootstrap.wizard_main.build_parser", return_value=parser) as parser_builder,
            patch("venus_evcharger.bootstrap.wizard_main.default_config_path", return_value=Path("/tmp/config.ini")),
            patch("venus_evcharger.bootstrap.wizard_main.default_template_path", return_value=Path("/tmp/template.ini")),
            patch("venus_evcharger.bootstrap.wizard_main.run_inventory_editor", return_value={"ok": True}) as editor,
            patch("venus_evcharger.bootstrap.wizard_main.print_inventory_action_result") as printer,
        ):
            self.assertEqual(wizard_main.main(["--inventory-action", "show"]), 0)
        parser_builder.assert_called_once_with("/tmp/config.ini", "/tmp/template.ini")
        editor.assert_called_once_with(inventory_namespace)
        printer.assert_called_once_with(inventory_namespace, {"ok": True})

        error_namespace = argparse.Namespace(inventory_action=None)
        parser.parse_args.return_value = error_namespace
        with (
            patch("venus_evcharger.bootstrap.wizard_main.build_parser", return_value=parser),
            patch("venus_evcharger.bootstrap.wizard_main.run_wizard", side_effect=ValueError("bad config")),
            patch("venus_evcharger.bootstrap.wizard_main.print_main_error") as print_error,
        ):
            self.assertEqual(wizard_main.main([]), 2)
        self.assertEqual(print_error.call_args.args[0], error_namespace)
        self.assertIsInstance(print_error.call_args.args[1], ValueError)
        self.assertEqual(str(print_error.call_args.args[1]), "bad config")

        result = _result()
        parser.parse_args.return_value = error_namespace
        with (
            patch("venus_evcharger.bootstrap.wizard_main.build_parser", return_value=parser),
            patch("venus_evcharger.bootstrap.wizard_main.run_wizard", return_value=result),
            patch("venus_evcharger.bootstrap.wizard_main.print_main_result") as print_result,
        ):
            self.assertEqual(wizard_main.main([]), 0)
        print_result.assert_called_once_with(error_namespace, result)

        missing_action_namespace = argparse.Namespace()
        parser.parse_args.return_value = missing_action_namespace
        with (
            patch("venus_evcharger.bootstrap.wizard_main.build_parser", return_value=parser),
            patch("venus_evcharger.bootstrap.wizard_main.run_wizard", return_value=result) as runner,
            patch("venus_evcharger.bootstrap.wizard_main.print_main_result"),
        ):
            self.assertEqual(wizard_main.main([]), 0)
        runner.assert_called_once_with(missing_action_namespace)

    def test_print_inventory_action_result_json_and_text_contracts(self) -> None:
        json_stdout = io.StringIO()
        with redirect_stdout(json_stdout):
            wizard_main.print_inventory_action_result(argparse.Namespace(json=True), {"z": 1, "a": True})
        self.assertEqual(json_stdout.getvalue(), '{\n  "a": true,\n  "z": 1\n}\n')

        text_stdout = io.StringIO()
        namespace = argparse.Namespace(json=False)
        inventory = object()
        with (
            patch("venus_evcharger.bootstrap.wizard_main.inventory_action_path", return_value=Path("/tmp/inventory.ini")) as path,
            patch("venus_evcharger.bootstrap.wizard_main.load_inventory", return_value=inventory) as load,
            patch("venus_evcharger.bootstrap.wizard_main.inventory_summary_text", return_value="summary text") as summary,
            redirect_stdout(text_stdout),
        ):
            wizard_main.print_inventory_action_result(namespace, {"ignored": True})
        path.assert_called_once_with(namespace)
        load.assert_called_once_with(Path("/tmp/inventory.ini"))
        summary.assert_called_once_with(Path("/tmp/inventory.ini"), inventory)
        self.assertEqual(text_stdout.getvalue(), "summary text\n")

    def test_print_main_error_and_result_json_and_text_contracts(self) -> None:
        json_stdout = io.StringIO()
        with redirect_stdout(json_stdout):
            wizard_main.print_main_error(argparse.Namespace(json=True), ValueError("broken"))
        self.assertEqual(json_stdout.getvalue(), '{\n  "code": "wizard-error",\n  "error": "broken"\n}\n')

        text_stderr = io.StringIO()
        with redirect_stderr(text_stderr):
            wizard_main.print_main_error(argparse.Namespace(json=False), ValueError("broken"))
        self.assertEqual(text_stderr.getvalue(), "Error: broken\n")

        result = _result()
        json_result_stdout = io.StringIO()
        with redirect_stdout(json_result_stdout):
            wizard_main.print_main_result(argparse.Namespace(json=True), result)
        self.assertEqual(json_result_stdout.getvalue(), json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n")

        text_result_stdout = io.StringIO()
        with (
            patch("venus_evcharger.bootstrap.wizard_main.result_text", return_value="result text") as formatter,
            redirect_stdout(text_result_stdout),
        ):
            wizard_main.print_main_result(argparse.Namespace(json=False), result)
        formatter.assert_called_once_with(result)
        self.assertEqual(text_result_stdout.getvalue(), "result text\n")


if __name__ == "__main__":
    unittest.main()
