# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
from pathlib import Path

from venus_evcharger.bootstrap.wizard import WizardAnswers, configure_wallbox, default_template_path


class _WizardSetupConfigValidationCases:
    def test_configure_wallbox_rejects_duplicate_source_ids_across_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            first_prefix = temp_path / "huawei-a"
            second_prefix = temp_path / "huawei-b"
            for bundle_prefix in (first_prefix, second_prefix):
                Path(str(bundle_prefix) + ".ini").write_text(
                    "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                    "AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini\n",
                    encoding="utf-8",
                )
                Path(str(bundle_prefix) + ".wizard.txt").write_text("Huawei recommendation\n", encoding="utf-8")
                Path(str(bundle_prefix) + ".summary.txt").write_text("Use source huawei\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "same source id: huawei"):
                configure_wallbox(
                    WizardAnswers(
                        profile="simple_relay",
                        host_input="192.0.2.44",
                        meter_host_input=None,
                        switch_host_input=None,
                        charger_host_input=None,
                        device_instance=61,
                        phase="L1",
                        policy_mode="manual",
                        digest_auth=False,
                        username="",
                        password="",
                        topology_preset=None,
                        charger_backend=None,
                        transport_kind="serial_rtu",
                        transport_host="192.0.2.44",
                        transport_port=502,
                        transport_device="/dev/ttyUSB0",
                        transport_unit_id=1,
                    ),
                    config_path=temp_path / "config.ini",
                    template_path=default_template_path(),
                    imported_from=None,
                    energy_recommendation_prefix=(str(first_prefix), str(second_prefix)),
                )

    def test_configure_wallbox_rejects_unknown_capacity_override_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bundle_prefix = temp_path / "huawei-a"
            Path(str(bundle_prefix) + ".ini").write_text(
                "AutoEnergySource.huawei_a.Profile=huawei_mb_unit1\n"
                "AutoEnergySource.huawei_a.ConfigPath=/data/etc/huawei-mb-unit1.ini\n",
                encoding="utf-8",
            )
            Path(str(bundle_prefix) + ".wizard.txt").write_text("Huawei recommendation A\n", encoding="utf-8")
            Path(str(bundle_prefix) + ".summary.txt").write_text("Use source huawei_a\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unknown source ids: huawei_b"):
                configure_wallbox(
                    WizardAnswers(
                        profile="simple_relay",
                        host_input="192.0.2.44",
                        meter_host_input=None,
                        switch_host_input=None,
                        charger_host_input=None,
                        device_instance=61,
                        phase="L1",
                        policy_mode="manual",
                        digest_auth=False,
                        username="",
                        password="",
                        topology_preset=None,
                        charger_backend=None,
                        transport_kind="serial_rtu",
                        transport_host="192.0.2.44",
                        transport_port=502,
                        transport_device="/dev/ttyUSB0",
                        transport_unit_id=1,
                    ),
                    config_path=temp_path / "config.ini",
                    template_path=default_template_path(),
                    imported_from=None,
                    energy_recommendation_prefix=str(bundle_prefix),
                    suggested_energy_capacity_overrides={"huawei_b": 7680.0},
                )
