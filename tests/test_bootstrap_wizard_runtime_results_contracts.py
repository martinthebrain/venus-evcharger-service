# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardChargerBackend, WizardResult, WizardTransportKind
from venus_evcharger.bootstrap import wizard_runtime_results


@dataclass(frozen=True)
class _NestedPayload:
    path: Path
    values: tuple[object, ...]


def _answers(
    *,
    charger_backend: WizardChargerBackend = "modbus_charger",
    transport_kind: WizardTransportKind = "tcp",
) -> WizardAnswers:
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
        charger_backend=charger_backend,
        charger_preset="custom",
        transport_kind=transport_kind,
        transport_host="charger.local",
        switch_group_supported_phase_selections="P1,P1_P2",
    )


def _result() -> WizardResult:
    return WizardResult(
        created_at="2026-07-07T23:30:00",
        config_path="/tmp/config.ini",
        imported_from="import.ini",
        profile="multi_adapter_topology",
        policy_mode="scheduled",
        topology_preset="goe-external-switch-group",
        charger_backend="modbus_charger",
        charger_preset="custom",
        transport_kind="tcp",
        role_hosts={"meter": "meter.local"},
        validation={"ok": True},
        live_check={"ok": True},
        warnings=("warning",),
        answer_defaults={"answer": "default"},
        generated_files=("config.ini",),
        backup_files=("backup.ini",),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        inventory_path="/tmp/inventory.ini",
        manual_review=("review",),
        dry_run=False,
        topology_config={"topology": "payload"},
        device_inventory={"inventory": "payload"},
        suggested_blocks={"block": "text"},
        suggested_energy_sources=({"id": "soc"},),
        suggested_energy_merge={"merge": True},
    )


class WizardRuntimeResultsContractTests(unittest.TestCase):
    def test_json_ready_converts_nested_dataclasses_paths_mappings_and_sequences(self) -> None:
        payload = _NestedPayload(
            path=Path("/tmp/config.ini"),
            values=(Path("/tmp/adapter.ini"), {4: [Path("/tmp/four.ini")]}),
        )

        self.assertEqual(
            wizard_runtime_results.json_ready(payload),
            {
                "path": "/tmp/config.ini",
                "values": ["/tmp/adapter.ini", {"4": ["/tmp/four.ini"]}],
            },
        )
        self.assertIs(wizard_runtime_results.json_ready(_NestedPayload), _NestedPayload)
        self.assertEqual(wizard_runtime_results.json_ready("plain"), "plain")

    def test_preview_result_preserves_complete_contract_and_computes_transport_defaults(self) -> None:
        answers = _answers()

        with patch("venus_evcharger.bootstrap.wizard_runtime_results.answer_defaults", return_value={"default": True}) as defaults:
            result = wizard_runtime_results.preview_result(
                answers,
                Path("/tmp/config.ini"),
                "2026-07-07T23:30:00",
                {"ok": True},
                {"live": True},
                {"topology": "payload"},
                {"inventory": "payload"},
                {"meter": "meter.local"},
                ("config.ini", "adapter.ini"),
                ("warning",),
                "import.ini",
                True,
                ("review",),
                {"block": "text"},
                ({"id": "soc"},),
                {"merge": True},
            )

        defaults.assert_called_once_with(answers)
        self.assertEqual(
            result,
            WizardResult(
                created_at="2026-07-07T23:30:00",
                config_path="/tmp/config.ini",
                imported_from="import.ini",
                profile="multi_adapter_topology",
                policy_mode="scheduled",
                topology_preset="goe-external-switch-group",
                charger_backend="modbus_charger",
                charger_preset="custom",
                transport_kind="tcp",
                role_hosts={"meter": "meter.local"},
                validation={"ok": True},
                live_check={"live": True},
                warnings=("warning",),
                answer_defaults={"default": True},
                generated_files=("config.ini", "adapter.ini"),
                backup_files=tuple(),
                result_path=None,
                audit_path=None,
                topology_summary_path=None,
                inventory_path=None,
                manual_review=("review",),
                dry_run=True,
                topology_config={"topology": "payload"},
                device_inventory={"inventory": "payload"},
                suggested_blocks={"block": "text"},
                suggested_energy_sources=({"id": "soc"},),
                suggested_energy_merge={"merge": True},
            ),
        )

    def test_preview_result_omits_transport_for_non_transport_backend(self) -> None:
        answers = _answers(charger_backend="goe_charger", transport_kind="serial_rtu")

        with patch("venus_evcharger.bootstrap.wizard_runtime_results.answer_defaults", return_value={}):
            result = wizard_runtime_results.preview_result(
                answers,
                Path("/tmp/config.ini"),
                "created",
                {},
                None,
                {},
                {},
                {},
                tuple(),
                tuple(),
                None,
                False,
                tuple(),
                {},
                tuple(),
                None,
            )

        self.assertIsNone(result.transport_kind)

    def test_preview_result_rejects_invalid_transport_with_transport_label(self) -> None:
        answers = _answers(charger_backend="modbus_charger", transport_kind=cast(WizardTransportKind, "bluetooth"))

        with patch("venus_evcharger.bootstrap.wizard_runtime_results.answer_defaults", return_value={}):
            with self.assertRaisesRegex(ValueError, r"^Unsupported transport: bluetooth$"):
                wizard_runtime_results.preview_result(
                    answers,
                    Path("/tmp/config.ini"),
                    "created",
                    {},
                    None,
                    {},
                    {},
                    {},
                    tuple(),
                    tuple(),
                    None,
                    False,
                    tuple(),
                    {},
                    tuple(),
                    None,
                )

    def test_persisted_result_attaches_state_paths_and_preserves_result_contract(self) -> None:
        original = _result()

        with patch(
            "venus_evcharger.bootstrap.wizard_runtime_results.persist_wizard_state",
            return_value=("/tmp/result.json", "/tmp/audit.json", "/tmp/topology.json"),
        ) as persist:
            result = wizard_runtime_results.persisted_result(original)

        persist.assert_called_once_with(Path("/tmp/config.ini"), original.as_dict())
        self.assertEqual(
            result,
            WizardResult(
                created_at=original.created_at,
                config_path=original.config_path,
                imported_from=original.imported_from,
                profile=original.profile,
                policy_mode=original.policy_mode,
                topology_preset=original.topology_preset,
                charger_backend=original.charger_backend,
                charger_preset=original.charger_preset,
                transport_kind=original.transport_kind,
                role_hosts=original.role_hosts,
                validation=original.validation,
                live_check=original.live_check,
                warnings=original.warnings,
                answer_defaults=original.answer_defaults,
                generated_files=original.generated_files,
                backup_files=original.backup_files,
                result_path="/tmp/result.json",
                audit_path="/tmp/audit.json",
                topology_summary_path="/tmp/topology.json",
                inventory_path=original.inventory_path,
                manual_review=original.manual_review,
                dry_run=original.dry_run,
                topology_config=original.topology_config,
                device_inventory=original.device_inventory,
                suggested_blocks=original.suggested_blocks,
                suggested_energy_sources=original.suggested_energy_sources,
                suggested_energy_merge=original.suggested_energy_merge,
            ),
        )


if __name__ == "__main__":
    unittest.main()
