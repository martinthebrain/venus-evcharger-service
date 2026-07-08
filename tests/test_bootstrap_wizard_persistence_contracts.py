# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, mock_open, patch

from venus_evcharger.bootstrap import wizard_persistence as persistence


def _payload(config_path: Path) -> dict[str, object]:
    return {
        "config_path": str(config_path),
        "profile": "multi_adapter_topology",
        "topology_preset": "template-stack",
        "charger_backend": "template_charger",
        "charger_preset": "generic",
        "policy_mode": "scheduled",
        "transport_kind": "tcp",
        "role_hosts": {
            "switch": "http://switch.local",
            "meter": "http://meter.local",
            "charger": "http://charger.local",
        },
        "validation": {"resolved_roles": {"meter": True, "switch": True, "charger": True}},
        "live_check": {"ok": True},
        "device_inventory": {
            "profiles": [{"id": "meter_profile"}],
            "devices": [{"id": "meter_device"}, {"id": "switch_device"}],
            "bindings": [{"id": "measurement"}],
        },
        "inventory_path": str(config_path.with_name("inventory.ini")),
        "suggested_energy_sources": [{"source_id": "grid"}, {}, "ignored", {"source_id": "pv"}],
        "suggested_energy_merge": {"merged_source_ids": ["grid", "pv"], "applied_to_config": True},
        "suggested_blocks": {"zeta": "[zeta]", "alpha": "[alpha]"},
        "warnings": ["check meter", "check switch"],
    }


class WizardPersistenceContractTests(unittest.TestCase):
    def test_wizard_artifact_paths_are_config_sidecars(self) -> None:
        config_path = Path("/data/etc/venus-evcharger/config.ini")
        self.assertEqual(persistence.result_path(config_path), Path("/data/etc/venus-evcharger/config.ini.wizard-result.json"))
        self.assertEqual(persistence.audit_path(config_path), Path("/data/etc/venus-evcharger/config.ini.wizard-audit.jsonl"))
        self.assertEqual(persistence.topology_summary_path(config_path), Path("/data/etc/venus-evcharger/config.ini.wizard-topology.txt"))
        self.assertEqual(persistence.inventory_path(config_path), Path("/data/etc/venus-evcharger/config.ini.wizard-inventory.ini"))

    def test_topology_summary_text_is_stable_and_sorted(self) -> None:
        config_path = Path("/tmp/config.ini")
        self.assertEqual(
            persistence._topology_summary_text(_payload(config_path)),
            "\n".join(
                [
                    "config_path: /tmp/config.ini",
                    "setup: multi_adapter_topology",
                    "topology_preset: template-stack",
                    "charger_backend: template_charger",
                    "charger_preset: generic",
                    "policy_mode: scheduled",
                    "transport_kind: tcp",
                    "role_endpoints:",
                    "  - charger: http://charger.local",
                    "  - meter: http://meter.local",
                    "  - switch: http://switch.local",
                    "resolved_roles: {'meter': True, 'switch': True, 'charger': True}",
                    "live_check_ok: True",
                    "inventory_counts: profiles=1 devices=2 bindings=1",
                    "inventory_path: /tmp/inventory.ini",
                    "suggested_energy_sources: grid, unknown, pv",
                    "suggested_energy_merge: grid,pv",
                    "suggested_energy_merge_applied: True",
                    "suggested_blocks: alpha, zeta",
                    "warnings:",
                    "  - check meter",
                    "  - check switch",
                    "",
                ]
            ),
        )

    def test_topology_summary_text_uses_fallbacks_and_omits_invalid_optional_sections(self) -> None:
        self.assertEqual(
            persistence._topology_summary_text(
                {
                    "config_path": "",
                    "profile": "",
                    "topology_preset": None,
                    "charger_backend": "",
                    "charger_preset": None,
                    "policy_mode": "",
                    "transport_kind": None,
                    "role_hosts": [],
                    "validation": [],
                    "live_check": [],
                    "device_inventory": [],
                    "suggested_energy_sources": [],
                    "suggested_energy_merge": [],
                    "suggested_blocks": [],
                    "warnings": [],
                }
            ),
            "\n".join(
                [
                    "config_path: ",
                    "setup: ",
                    "topology_preset: n/a",
                    "charger_backend: none",
                    "charger_preset: n/a",
                    "policy_mode: ",
                    "transport_kind: n/a",
                    "role_endpoints:",
                    "  - none",
                    "",
                ]
            ),
        )
        self.assertNotIn(
            "role_endpoints:\n  - 0:",
            persistence._topology_summary_text({**_payload(Path("/tmp/config.ini")), "role_hosts": ["not", "a", "dict"]}),
        )
        type_guard_summary = persistence._topology_summary_text(
            {
                **_payload(Path("/tmp/config.ini")),
                "device_inventory": {"profiles": "bad", "devices": {"bad": True}, "bindings": ("bad",)},
                "suggested_blocks": ["not-a-dict"],
                "suggested_energy_sources": ({"source_id": "tuple-source"},),
            }
        )
        self.assertIn("inventory_counts: profiles=0 devices=0 bindings=0", type_guard_summary)
        self.assertNotIn("suggested_blocks:", type_guard_summary)
        self.assertNotIn("tuple-source", type_guard_summary)

    def test_persist_wizard_state_writes_result_audit_and_summary_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result_path, audit_path, topology_path = persistence.persist_wizard_state(config_path, _payload(config_path))

            expected_result_path = str(config_path.with_name("config.ini.wizard-result.json"))
            expected_audit_path = str(config_path.with_name("config.ini.wizard-audit.jsonl"))
            expected_topology_path = str(config_path.with_name("config.ini.wizard-topology.txt"))
            self.assertEqual((result_path, audit_path, topology_path), (expected_result_path, expected_audit_path, expected_topology_path))

            result_payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
            self.assertEqual(result_payload["result_path"], expected_result_path)
            self.assertEqual(result_payload["audit_path"], expected_audit_path)
            self.assertEqual(result_payload["topology_summary_path"], expected_topology_path)
            self.assertEqual(
                Path(result_path).read_text(encoding="utf-8"),
                json.dumps(result_payload, indent=2, sort_keys=True) + "\n",
            )
            self.assertEqual(Path(audit_path).read_text(encoding="utf-8"), json.dumps(result_payload, sort_keys=True) + "\n")
            self.assertIn("role_endpoints:\n  - charger: http://charger.local", Path(topology_path).read_text(encoding="utf-8"))

    def test_persist_wizard_state_uses_explicit_utf8_for_all_artifacts(self) -> None:
        config_path = Path("/tmp/config.ini")
        audit_open = mock_open()
        with (
            patch.object(Path, "write_text", return_value=1) as write_text,
            patch.object(Path, "open", audit_open),
        ):
            persistence.persist_wizard_state(config_path, _payload(config_path))

        self.assertEqual(len(write_text.call_args_list), 2)
        self.assertEqual(write_text.call_args_list[0].kwargs["encoding"], "utf-8")
        self.assertEqual(write_text.call_args_list[1].kwargs["encoding"], "utf-8")
        self.assertIn("\n  ", write_text.call_args_list[0].args[0])
        self.assertEqual(audit_open.call_args, call("a", encoding="utf-8"))
        audit_open().write.assert_called_once()

    def test_persist_inventory_sidecar_writes_utf8_sidecar_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            path = persistence.persist_inventory_sidecar(config_path, "[Inventory]\nName=ä\n")

            self.assertEqual(path, str(config_path.with_name("config.ini.wizard-inventory.ini")))
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "[Inventory]\nName=ä\n")

    def test_persist_inventory_sidecar_uses_explicit_utf8(self) -> None:
        config_path = Path("/tmp/config.ini")
        with patch.object(Path, "write_text", return_value=17) as write_text:
            self.assertEqual(
                persistence.persist_inventory_sidecar(config_path, "[Inventory]\nName=ä\n"),
                "/tmp/config.ini.wizard-inventory.ini",
            )

        write_text.assert_called_once_with("[Inventory]\nName=ä\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
