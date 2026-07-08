# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardResult
from venus_evcharger.bootstrap.wizard_runtime import configure_wallbox


def _answers() -> WizardAnswers:
    return WizardAnswers(
        profile="multi_adapter_topology",
        host_input="primary.local",
        meter_host_input="meter.local",
        switch_host_input="switch.local",
        charger_host_input="charger.local",
        device_instance=71,
        phase="L2",
        policy_mode="scheduled",
        digest_auth=True,
        username="user",
        password="secret",
        topology_preset="goe-external-switch-group",
        charger_backend="goe_charger",
        charger_preset="go-e-v3",
        transport_kind="tcp",
        transport_host="charger.local",
        switch_group_supported_phase_selections="P1,P1_P2",
    )


def _preview_result(*, dry_run: bool, live_check: dict[str, object] | None = None) -> WizardResult:
    return WizardResult(
        created_at="2026-07-07T21:00:00",
        config_path="/tmp/config.ini",
        imported_from="import.ini",
        profile="multi_adapter_topology",
        policy_mode="scheduled",
        topology_preset="goe-external-switch-group",
        charger_backend="goe_charger",
        charger_preset="go-e-v3",
        transport_kind="tcp",
        role_hosts={"meter": "meter.local"},
        validation={"ok": True},
        live_check=live_check,
        generated_files=("config.ini",),
        backup_files=tuple(),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        manual_review=("review",),
        dry_run=dry_run,
    )


class WizardRuntimeContractTests(unittest.TestCase):
    def test_configure_wallbox_passes_runtime_contracts_without_live_check(self) -> None:
        answers = _answers()
        config_path = Path("/tmp/runtime-contract.ini")
        template_path = Mock()
        template_path.read_text.return_value = "template text"
        preview = _preview_result(dry_run=True)

        with (
            patch("venus_evcharger.bootstrap.wizard_runtime.render_wizard_config", return_value=("config", {"b.ini": "B"}, {"meter": "meter.local"})) as render,
            patch("venus_evcharger.bootstrap.wizard_runtime.manual_review_items", return_value=("review",)) as review,
            patch("venus_evcharger.bootstrap.wizard_runtime.merged_recommendation_prefixes", return_value=("energy", "huawei")) as prefixes,
            patch(
                "venus_evcharger.bootstrap.wizard_runtime.suggested_energy_state",
                return_value=("config2", {"a.ini": "A"}, ("review", "energy"), {"block": "text"}, ({"id": "soc"},), {"merge": True}),
            ) as energy,
            patch("venus_evcharger.bootstrap.wizard_runtime.validate_rendered_setup", return_value={"ok": True}) as validate,
            patch("venus_evcharger.bootstrap.wizard_runtime.live_check_rendered_setup") as default_live_check,
            patch("venus_evcharger.bootstrap.wizard_runtime.build_wizard_topology_config", return_value=SimpleNamespace(as_dict=lambda: {"top": "cfg"})) as topology,
            patch("venus_evcharger.bootstrap.wizard_runtime.json_ready_dict", return_value={"top": "payload"}) as json_ready,
            patch("venus_evcharger.bootstrap.wizard_runtime.build_wizard_inventory", return_value={"inventory": "raw"}) as inventory,
            patch("venus_evcharger.bootstrap.wizard_runtime.inventory_payload", return_value={"inventory": "payload"}) as inventory_payload,
            patch("venus_evcharger.bootstrap.wizard_runtime.inventory_text", return_value="inventory text") as inventory_text,
            patch("venus_evcharger.bootstrap.wizard_runtime.materialized_config_text", return_value="materialized") as materialized,
            patch("venus_evcharger.bootstrap.wizard_runtime.compatibility_warnings", return_value=("warning",)) as warnings,
            patch("venus_evcharger.bootstrap.wizard_runtime.preview_result", return_value=preview) as preview_builder,
        ):
            result = configure_wallbox(
                answers,
                config_path=config_path,
                template_path=template_path,
                dry_run=True,
                imported_from="import.ini",
                energy_recommendation_prefix="energy",
                huawei_recommendation_prefix="huawei",
                suggested_energy_capacity_wh=12000.0,
                suggested_energy_capacity_overrides={"soc": 8000.0},
            )

        self.assertIs(result, preview)
        template_path.read_text.assert_called_once_with(encoding="utf-8")
        render.assert_called_once_with("template text", answers)
        review.assert_called_once_with(
            answers.profile,
            answers.policy_mode,
            answers.charger_backend,
            answers.transport_kind,
            answers.topology_preset,
        )
        prefixes.assert_called_once_with("energy", "huawei")
        energy.assert_called_once_with(
            config_path,
            "config",
            {"b.ini": "B"},
            ("review",),
            ("energy", "huawei"),
            apply_suggested_energy_merge=False,
            suggested_energy_capacity_wh=12000.0,
            suggested_energy_capacity_overrides={"soc": 8000.0},
        )
        validate.assert_called_once_with("config2", {"a.ini": "A"}, "runtime-contract.ini")
        default_live_check.assert_not_called()
        topology.assert_called_once_with(answers)
        topology_config = topology.return_value
        json_ready.assert_called_once_with(topology_config, "topology config")
        inventory.assert_called_once_with(answers, {"meter": "meter.local"}, topology_config)
        inventory_payload.assert_called_once_with({"inventory": "raw"})
        inventory_text.assert_called_once_with(answers, {"meter": "meter.local"}, topology_config)
        materialized.assert_called_once_with("config2", config_path.parent, {"a.ini": "A"})
        warnings.assert_called_once_with(
            profile=answers.profile,
            topology_preset=answers.topology_preset,
            charger_backend=answers.charger_backend,
            charger_preset=answers.charger_preset,
            primary_host_input=answers.host_input,
            role_hosts={"meter": "meter.local"},
            transport_kind=answers.transport_kind,
            transport_host=answers.transport_host,
            switch_group_supported_phase_selections=answers.switch_group_supported_phase_selections,
        )
        preview_args = preview_builder.call_args.args
        self.assertIs(preview_args[0], answers)
        self.assertEqual(preview_args[1], config_path)
        self.assertIsInstance(preview_args[2], str)
        self.assertEqual(preview_args[3], {"ok": True})
        self.assertIsNone(preview_args[4])
        self.assertEqual(preview_args[5], {"top": "payload"})
        self.assertEqual(preview_args[6], {"inventory": "payload"})
        self.assertEqual(preview_args[7], {"meter": "meter.local"})
        self.assertEqual(preview_args[8], ("runtime-contract.ini", "a.ini"))
        self.assertEqual(preview_args[9], ("warning",))
        self.assertEqual(preview_args[10], "import.ini")
        self.assertIs(preview_args[11], True)
        self.assertEqual(preview_args[12], ("review", "energy"))
        self.assertEqual(preview_args[13], {"block": "text"})
        self.assertEqual(preview_args[14], ({"id": "soc"},))
        self.assertEqual(preview_args[15], {"merge": True})

    def test_configure_wallbox_passes_live_check_arguments(self) -> None:
        answers = _answers()
        config_path = Path("/tmp/runtime-live.ini")
        template_path = Mock()
        template_path.read_text.return_value = "template text"
        live_runner = Mock(return_value={"ok": True})

        with (
            patch("venus_evcharger.bootstrap.wizard_runtime.render_wizard_config", return_value=("config", {"adapter.ini": "A"}, {})),
            patch("venus_evcharger.bootstrap.wizard_runtime.manual_review_items", return_value=tuple()),
            patch("venus_evcharger.bootstrap.wizard_runtime.merged_recommendation_prefixes", return_value=tuple()),
            patch(
                "venus_evcharger.bootstrap.wizard_runtime.suggested_energy_state",
                return_value=("config", {"adapter.ini": "A"}, tuple(), {}, tuple(), None),
            ),
            patch("venus_evcharger.bootstrap.wizard_runtime.validate_rendered_setup", return_value={"ok": True}),
            patch("venus_evcharger.bootstrap.wizard_runtime.build_wizard_topology_config", return_value={}),
            patch("venus_evcharger.bootstrap.wizard_runtime.json_ready_dict", return_value={}),
            patch("venus_evcharger.bootstrap.wizard_runtime.build_wizard_inventory", return_value={}),
            patch("venus_evcharger.bootstrap.wizard_runtime.inventory_payload", return_value={}),
            patch("venus_evcharger.bootstrap.wizard_runtime.inventory_text", return_value=""),
            patch("venus_evcharger.bootstrap.wizard_runtime.materialized_config_text", return_value="materialized"),
            patch("venus_evcharger.bootstrap.wizard_runtime.compatibility_warnings", return_value=tuple()),
            patch("venus_evcharger.bootstrap.wizard_runtime.preview_result", return_value=_preview_result(dry_run=True, live_check={"ok": True})),
        ):
            configure_wallbox(
                answers,
                config_path=config_path,
                template_path=template_path,
                dry_run=True,
                live_check=True,
                selected_probe_roles=("meter",),
                live_check_runner=live_runner,
            )

        live_runner.assert_called_once_with("config", {"adapter.ini": "A"}, "runtime-live.ini", ("meter",))


if __name__ == "__main__":
    unittest.main()
