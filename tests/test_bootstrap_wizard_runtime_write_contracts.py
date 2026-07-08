# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap import wizard_runtime_write


def _namespace(**overrides: object) -> object:
    values = {"dry_run": False, "force": False, "non_interactive": False, "yes": False}
    values.update(overrides)
    return SimpleNamespace(**values)


def _result() -> WizardResult:
    return WizardResult(
        created_at="2026-07-07T23:00:00",
        config_path="/tmp/config.ini",
        imported_from=None,
        profile="simple_relay",
        policy_mode="manual",
        topology_preset=None,
        charger_backend=None,
        charger_preset=None,
        transport_kind=None,
        role_hosts={},
        validation={"ok": True},
        live_check=None,
        generated_files=("config.ini",),
        backup_files=tuple(),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        inventory_path=None,
        manual_review=tuple(),
        dry_run=True,
    )


class WizardRuntimeWriteContractTests(unittest.TestCase):
    def test_existing_output_paths_returns_existing_absolute_paths_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            existing_adapter = Path(temp_dir) / "adapter.ini"
            existing_adapter.write_text("old", encoding="utf-8")

            self.assertEqual(
                wizard_runtime_write.existing_output_paths(config_path, ("adapter.ini", "missing.ini")),
                (str(existing_adapter),),
            )

    def test_interactive_write_confirmed_prints_preview_and_uses_exact_prompt_contracts(self) -> None:
        preview = _result()
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_output.result_text", return_value="PREVIEW TEXT") as result_text,
            patch("venus_evcharger.bootstrap.wizard_runtime_write.prompt_yes_no", return_value=True) as prompt,
            patch("builtins.print") as printed,
        ):
            self.assertTrue(wizard_runtime_write.interactive_write_confirmed(preview, ("existing.ini",)))

        result_text.assert_called_once_with(preview)
        printed.assert_called_once_with("PREVIEW TEXT")
        prompt.assert_called_once_with("Write config now and create backups of existing files?", False)

        with (
            patch("venus_evcharger.bootstrap.wizard_cli_output.result_text", return_value="PREVIEW TEXT"),
            patch("venus_evcharger.bootstrap.wizard_runtime_write.prompt_yes_no", return_value=True) as prompt,
            patch("builtins.print"),
        ):
            self.assertTrue(wizard_runtime_write.interactive_write_confirmed(preview, tuple()))

        prompt.assert_called_once_with("Write config now?", True)

    def test_non_interactive_write_allowed_has_exact_force_and_error_contract(self) -> None:
        self.assertFalse(wizard_runtime_write.non_interactive_write_allowed(_namespace(), tuple()))
        self.assertTrue(
            wizard_runtime_write.non_interactive_write_allowed(
                _namespace(non_interactive=True, force=True),
                ("existing.ini", "adapter.ini"),
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            r"^Refusing to overwrite existing files in --non-interactive mode without --force: existing\.ini, adapter\.ini$",
        ):
            wizard_runtime_write.non_interactive_write_allowed(
                _namespace(non_interactive=True),
                ("existing.ini", "adapter.ini"),
            )

    def test_skip_write_confirmation_preserves_decision_order_and_arguments(self) -> None:
        namespace = _namespace(yes=True)
        with patch("venus_evcharger.bootstrap.wizard_runtime_write.non_interactive_write_allowed", return_value=False) as gate:
            self.assertTrue(wizard_runtime_write.skip_write_confirmation(namespace, ("existing.ini",)))

        gate.assert_called_once_with(namespace, ("existing.ini",))

        with patch("venus_evcharger.bootstrap.wizard_runtime_write.non_interactive_write_allowed") as gate:
            self.assertTrue(wizard_runtime_write.skip_write_confirmation(_namespace(dry_run=True), ("existing.ini",)))

        gate.assert_not_called()

    def test_confirm_write_uses_skip_then_interactive_gate_with_exact_error(self) -> None:
        namespace = _namespace()
        preview = _result()
        existing = ("existing.ini",)

        with (
            patch("venus_evcharger.bootstrap.wizard_runtime_write.skip_write_confirmation", return_value=False) as skip,
            patch("venus_evcharger.bootstrap.wizard_runtime_write.interactive_write_confirmed", return_value=True) as interactive,
        ):
            wizard_runtime_write.confirm_write(namespace, preview, existing)

        skip.assert_called_once_with(namespace, existing)
        interactive.assert_called_once_with(preview, existing)

        with (
            patch("venus_evcharger.bootstrap.wizard_runtime_write.skip_write_confirmation", return_value=True) as skip,
            patch("venus_evcharger.bootstrap.wizard_runtime_write.interactive_write_confirmed") as interactive,
        ):
            wizard_runtime_write.confirm_write(namespace, preview, existing)

        skip.assert_called_once_with(namespace, existing)
        interactive.assert_not_called()

        with (
            patch("venus_evcharger.bootstrap.wizard_runtime_write.skip_write_confirmation", return_value=False),
            patch("venus_evcharger.bootstrap.wizard_runtime_write.interactive_write_confirmed", return_value=False),
        ):
            with self.assertRaisesRegex(ValueError, r"^Wizard write cancelled by user$"):
                wizard_runtime_write.confirm_write(namespace, preview, existing)


if __name__ == "__main__":
    unittest.main()
