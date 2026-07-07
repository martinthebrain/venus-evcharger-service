# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.bootstrap import wizard_cli_imports as imports
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults


class BootstrapWizardCliImportsTests(unittest.TestCase):
    def test_empty_imported_defaults_contract(self) -> None:
        self.assertEqual(
            imports.empty_imported_defaults(),
            ImportedWizardDefaults(
                imported_from="",
                profile=None,
                host_input=None,
                meter_host_input=None,
                switch_host_input=None,
                charger_host_input=None,
                device_instance=None,
                phase=None,
                policy_mode=None,
                digest_auth=None,
                username=None,
                password=None,
                topology_preset=None,
                charger_backend=None,
                charger_preset=None,
                request_timeout_seconds=None,
                switch_group_phase_layout=None,
                auto_start_surplus_watts=None,
                auto_stop_surplus_watts=None,
                auto_min_soc=None,
                auto_resume_soc=None,
                scheduled_enabled_days=None,
                scheduled_latest_end_time=None,
                scheduled_night_current_amps=None,
                transport_kind=None,
                transport_host=None,
                transport_port=None,
                transport_device=None,
                transport_unit_id=None,
                inventory_path=None,
            ),
        )

    def test_resume_and_clone_paths_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result_path = Path(f"{config_path}.wizard-result.json")
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            result_path.write_text("{}", encoding="utf-8")

            self.assertIsNone(imports.resume_import_path(_namespace(config_path=config_path, resume_last=False)))
            self.assertEqual(imports.resume_import_path(_namespace(config_path=config_path, resume_last=True)), result_path)
            self.assertIsNone(imports.clone_import_path(_namespace(config_path=config_path, clone_current=False)))
            self.assertEqual(imports.clone_import_path(_namespace(config_path=config_path, clone_current=True)), config_path)

        missing_config = Path(tempfile.gettempdir()) / "missing-wizard-config.ini"
        with self.assertRaises(ValueError) as missing_resume:
            imports.resume_import_path(_namespace(config_path=missing_config, resume_last=True))
        self.assertEqual(
            str(missing_resume.exception),
            f"--resume-last requested but no prior wizard result exists: {missing_config}.wizard-result.json",
        )
        with self.assertRaises(ValueError) as missing_clone:
            imports.clone_import_path(_namespace(config_path=missing_config, clone_current=True))
        self.assertEqual(
            str(missing_clone.exception),
            f"--clone-current requested but config does not exist: {missing_config}",
        )

    def test_resolve_import_path_priority_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result_path = Path(f"{config_path}.wizard-result.json")
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            result_path.write_text("{}", encoding="utf-8")

            self.assertEqual(
                imports.resolve_import_path(
                    _namespace(
                        config_path=config_path,
                        import_config="/tmp/explicit-result.json",
                        resume_last=True,
                        clone_current=True,
                    )
                ),
                Path("/tmp/explicit-result.json"),
            )
            self.assertEqual(imports.resolve_import_path(_namespace(config_path=config_path, resume_last=True)), result_path)
            self.assertEqual(imports.resolve_import_path(_namespace(config_path=config_path, clone_current=True)), config_path)
            self.assertIsNone(imports.resolve_import_path(_namespace(config_path=config_path)))

    def test_resolve_imported_defaults_contract(self) -> None:
        namespace = _namespace(import_config=None, resume_last=False, clone_current=False)
        self.assertIsNone(imports.resolve_imported_defaults(namespace))

        expected = imports.empty_imported_defaults()
        namespace = _namespace(import_config="/tmp/import.json")
        with patch("venus_evcharger.bootstrap.wizard_cli_imports.load_imported_defaults", return_value=expected) as loader:
            self.assertIs(imports.resolve_imported_defaults(namespace), expected)
        loader.assert_called_once_with(Path("/tmp/import.json"))


def _namespace(
    *,
    config_path: str | Path = "/tmp/config.ini",
    import_config: str | None = None,
    resume_last: bool = False,
    clone_current: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        config_path=str(config_path),
        import_config=import_config,
        resume_last=resume_last,
        clone_current=clone_current,
    )


if __name__ == "__main__":
    unittest.main()
