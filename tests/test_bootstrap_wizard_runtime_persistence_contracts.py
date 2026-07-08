# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_runtime_persistence import wizard_persisted_result


def _result() -> WizardResult:
    return WizardResult(
        created_at="2026-07-07T22:00:00",
        config_path="/old/config.ini",
        imported_from="import.ini",
        profile="multi_adapter_topology",
        policy_mode="scheduled",
        topology_preset="goe-external-switch-group",
        charger_backend="goe_charger",
        charger_preset="go-e-v3",
        transport_kind="tcp",
        role_hosts={"meter": "meter.local"},
        validation={"ok": True},
        live_check={"ok": True},
        generated_files=("old.ini",),
        backup_files=("old.bak",),
        result_path="old-result.json",
        audit_path="old-audit.json",
        topology_summary_path="old-topology.json",
        inventory_path="old-inventory.ini",
        manual_review=("review",),
        dry_run=False,
        topology_config={"topology": "payload"},
        device_inventory={"inventory": "payload"},
        warnings=("warning",),
        answer_defaults={"answer": "default"},
        suggested_blocks={"block": "text"},
        suggested_energy_sources=({"id": "soc"},),
        suggested_energy_merge={"merge": True},
    )


class WizardRuntimePersistenceContractTests(unittest.TestCase):
    def test_wizard_persisted_result_writes_outputs_and_preserves_result_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "nested" / "child" / "config.ini"
            persisted = Mock(side_effect=lambda staged: staged)

            with (
                patch(
                    "venus_evcharger.bootstrap.wizard_runtime_persistence.write_generated_files",
                    return_value=["config.ini.bak", "adapter.ini.bak"],
                ) as write_generated,
                patch(
                    "venus_evcharger.bootstrap.wizard_runtime_persistence.persist_inventory_sidecar",
                    return_value="/tmp/inventory.ini",
                ) as persist_inventory,
                patch("venus_evcharger.bootstrap.wizard_runtime_persistence.persisted_result", persisted),
            ):
                result = wizard_persisted_result(
                    _result(),
                    config_path,
                    "materialized config",
                    {"adapter.ini": "adapter text"},
                    "inventory text",
                )
                self.assertTrue(config_path.parent.exists())

        write_generated.assert_called_once_with(
            config_path,
            "materialized config",
            {"adapter.ini": "adapter text"},
        )
        persist_inventory.assert_called_once_with(config_path, "inventory text")
        persisted.assert_called_once()
        self.assertIs(result, persisted.call_args.args[0])
        self.assertEqual(
            result,
            WizardResult(
                created_at="2026-07-07T22:00:00",
                config_path="/old/config.ini",
                imported_from="import.ini",
                profile="multi_adapter_topology",
                policy_mode="scheduled",
                topology_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                charger_preset="go-e-v3",
                transport_kind="tcp",
                role_hosts={"meter": "meter.local"},
                validation={"ok": True},
                live_check={"ok": True},
                generated_files=("old.ini",),
                backup_files=("config.ini.bak", "adapter.ini.bak"),
                result_path=None,
                audit_path=None,
                topology_summary_path=None,
                inventory_path="/tmp/inventory.ini",
                manual_review=("review",),
                dry_run=False,
                topology_config={"topology": "payload"},
                device_inventory={"inventory": "payload"},
                warnings=("warning",),
                answer_defaults={"answer": "default"},
                suggested_blocks={"block": "text"},
                suggested_energy_sources=({"id": "soc"},),
                suggested_energy_merge={"merge": True},
            ),
        )

    def test_wizard_persisted_result_accepts_preexisting_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "existing" / "config.ini"
            config_path.parent.mkdir()

            with (
                patch(
                    "venus_evcharger.bootstrap.wizard_runtime_persistence.write_generated_files",
                    return_value=[],
                ),
                patch(
                    "venus_evcharger.bootstrap.wizard_runtime_persistence.persist_inventory_sidecar",
                    return_value="/tmp/inventory.ini",
                ),
                patch(
                    "venus_evcharger.bootstrap.wizard_runtime_persistence.persisted_result",
                    side_effect=lambda staged: staged,
                ),
            ):
                result = wizard_persisted_result(_result(), config_path, "config", {}, "inventory")

        self.assertEqual(result.backup_files, ())
        self.assertEqual(result.inventory_path, "/tmp/inventory.ini")


if __name__ == "__main__":
    unittest.main()
