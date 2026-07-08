# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap.wizard_topology_render import render_adapter_files_from_topology
from venus_evcharger.topology import (
    ActuatorConfig,
    ActuatorType,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
    TopologyType,
)


def _answers(*, topology_preset: str | None = None, charger_preset: str | None = None) -> WizardAnswers:
    return WizardAnswers(
        profile="multi_adapter_topology",
        host_input="shared.local",
        meter_host_input="meter.local",
        switch_host_input="switch.local",
        charger_host_input="charger.local",
        device_instance=71,
        phase="L1",
        policy_mode="manual",
        digest_auth=False,
        username="",
        password="",
        topology_preset=topology_preset,
        charger_backend="modbus_charger",
        charger_preset=charger_preset,
        request_timeout_seconds=6.5,
        transport_kind="tcp",
        transport_host="modbus.local",
        transport_port=1502,
        transport_device="/dev/ttyUSB9",
        transport_unit_id=9,
        cerbo_relay_index=2,
        cerbo_relay_contact_mode="nc",
        switch_group_supported_phase_selections="P1,P1_P2",
    )


def _topology(
    *,
    topology_type: TopologyType,
    actuator: ActuatorConfig | None = None,
    measurement: MeasurementConfig | None = None,
    charger: ChargerConfig | None = None,
) -> EvChargerTopologyConfig:
    return EvChargerTopologyConfig(
        topology=TopologyConfig(type=topology_type),
        actuator=actuator,
        measurement=measurement,
        charger=charger,
        policy=PolicyConfig(mode="manual", phase="L1"),
    )


class WizardTopologyRenderContractTests(unittest.TestCase):
    def test_unknown_or_incomplete_topologies_render_no_files(self) -> None:
        answers = _answers()
        self.assertEqual(render_adapter_files_from_topology(_topology(topology_type="custom_topology"), answers, {}), {})
        self.assertEqual(render_adapter_files_from_topology(_topology(topology_type="native_device"), answers, {}), {})
        self.assertEqual(render_adapter_files_from_topology(_topology(topology_type="hybrid_topology"), answers, {}), {})

    def test_charger_rendering_covers_template_modbus_preset_and_native_url_paths(self) -> None:
        role_hosts = {"charger": "charger.local"}
        self.assertIn(
            "BaseUrl=http://charger.local",
            render_adapter_files_from_topology(
                _topology(topology_type="native_device", charger=ChargerConfig(type="template_charger")),
                _answers(),
                role_hosts,
            )["wizard-charger.ini"],
        )
        modbus_config = render_adapter_files_from_topology(
            _topology(topology_type="native_device", charger=ChargerConfig(type="modbus_charger")),
            _answers(),
            role_hosts,
        )["wizard-charger.ini"]
        self.assertIn("Type=modbus_charger", modbus_config)
        self.assertIn("Transport=tcp", modbus_config)
        self.assertIn("Host=modbus.local", modbus_config)
        self.assertIn("Port=1502", modbus_config)
        self.assertIn("UnitId=9", modbus_config)

        preset_config = render_adapter_files_from_topology(
            _topology(topology_type="native_device", charger=ChargerConfig(type="modbus_charger")),
            _answers(charger_preset="abb-terra-ac-modbus"),
            role_hosts,
        )["wizard-charger.ini"]
        self.assertIn("Profile=generic", preset_config)
        self.assertIn("Preset=abb-terra-ac-modbus", preset_config)
        self.assertNotIn("BaseUrl=http://charger.local", preset_config)

        native_config = render_adapter_files_from_topology(
            _topology(topology_type="native_device", charger=ChargerConfig(type="goe_charger")),
            _answers(),
            role_hosts,
        )["wizard-charger.ini"]
        self.assertIn("Type=goe_charger", native_config)
        self.assertIn("BaseUrl=http://charger.local", native_config)

    def test_charger_renderer_dispatch_passes_exact_arguments(self) -> None:
        role_hosts = {"charger": "charger.local"}
        with patch("venus_evcharger.bootstrap.wizard_topology_render.template_charger_config", return_value="template!") as renderer:
            self.assertEqual(
                render_adapter_files_from_topology(
                    _topology(topology_type="native_device", charger=ChargerConfig(type="template_charger")),
                    _answers(),
                    role_hosts,
                )["wizard-charger.ini"],
                "template!",
            )
        renderer.assert_called_once_with("http://charger.local")

        with patch("venus_evcharger.bootstrap.wizard_topology_render.modbus_charger_config", return_value="modbus!") as renderer:
            self.assertEqual(
                render_adapter_files_from_topology(
                    _topology(topology_type="native_device", charger=ChargerConfig(type="modbus_charger")),
                    _answers(),
                    role_hosts,
                )["wizard-charger.ini"],
                "modbus!",
            )
        renderer.assert_called_once_with(
            "tcp",
            transport_host="modbus.local",
            transport_port=1502,
            transport_device="/dev/ttyUSB9",
            transport_unit_id=9,
        )

        with patch("venus_evcharger.bootstrap.wizard_topology_render.native_charger_config", return_value="native!") as renderer:
            self.assertEqual(
                render_adapter_files_from_topology(
                    _topology(topology_type="native_device", charger=ChargerConfig(type="modbus_charger")),
                    _answers(charger_preset="abb-terra-ac-modbus"),
                    role_hosts,
                )["wizard-charger.ini"],
                "native!",
            )
        renderer.assert_called_once_with(
            "modbus_charger",
            "",
            charger_preset="abb-terra-ac-modbus",
            request_timeout_seconds=6.5,
            transport_kind="tcp",
            transport_host="modbus.local",
            transport_port=1502,
            transport_device="/dev/ttyUSB9",
            transport_unit_id=9,
        )

        with patch("venus_evcharger.bootstrap.wizard_topology_render.native_charger_config", return_value="native!") as renderer:
            self.assertEqual(
                render_adapter_files_from_topology(
                    _topology(topology_type="native_device", charger=ChargerConfig(type="goe_charger")),
                    _answers(),
                    role_hosts,
                )["wizard-charger.ini"],
                "native!",
            )
        renderer.assert_called_once_with(
            "goe_charger",
            "http://charger.local",
            charger_preset=None,
            request_timeout_seconds=6.5,
            transport_kind="tcp",
            transport_host="modbus.local",
            transport_port=1502,
            transport_device="/dev/ttyUSB9",
            transport_unit_id=9,
        )

    def test_meter_family_selection_uses_topology_preset_and_meter_host(self) -> None:
        cases = {
            "template-stack": ("Type=template_meter", "BaseUrl=http://meter.local"),
            None: ("Type=template_meter", "BaseUrl=http://meter.local"),
            "shelly-meter-goe": ("Type=shelly_meter", "Host=meter.local"),
            "tasmota-meter-goe": ("Type=tasmota_meter", "BaseUrl=http://meter.local"),
            "tuya-meter-goe": ("Type=tuya_meter", "BaseUrl=http://meter.local"),
        }
        topology = _topology(topology_type="native_device", measurement=MeasurementConfig(type="external_meter"), charger=ChargerConfig(type="goe_charger"))
        for preset, expected_fragments in cases.items():
            with self.subTest(preset=preset):
                meter_config = render_adapter_files_from_topology(topology, _answers(topology_preset=preset), {"meter": "meter.local"})["wizard-meter.ini"]
                for fragment in expected_fragments:
                    self.assertIn(fragment, meter_config)

        fallback_config = render_adapter_files_from_topology(
            topology,
            _answers(topology_preset="shelly-meter-goe"),
            {},
        )["wizard-meter.ini"]
        self.assertIn("Host=shared.local", fallback_config)

    def test_simple_relay_external_meter_and_cerbo_actuator_render_separate_files(self) -> None:
        files = render_adapter_files_from_topology(
            _topology(
                topology_type="simple_relay",
                measurement=MeasurementConfig(type="external_meter"),
                actuator=ActuatorConfig(type="cerbo_gx_relay_switch", config_path="wizard-switch.ini"),
            ),
            _answers(topology_preset="template-meter-cerbo-relay"),
            {"meter": "meter.local", "switch": "ignored.local"},
        )
        self.assertEqual(set(files), {"wizard-meter.ini", "wizard-switch.ini"})
        self.assertIn("Type=template_meter", files["wizard-meter.ini"])
        self.assertIn("Type=cerbo_gx_relay_switch", files["wizard-switch.ini"])
        self.assertIn("RelayIndex=2", files["wizard-switch.ini"])
        self.assertIn("ContactMode=NC", files["wizard-switch.ini"])

    def test_simple_relay_skips_non_external_meter_and_configless_actuator(self) -> None:
        self.assertEqual(
            render_adapter_files_from_topology(
                _topology(
                    topology_type="simple_relay",
                    measurement=MeasurementConfig(type="actuator_native"),
                    actuator=ActuatorConfig(type="shelly_switch", config_path=None),
                ),
                _answers(),
                {},
            ),
            {},
        )

    def test_actuator_renderer_matrix_uses_switch_host_and_expected_filenames(self) -> None:
        cases: dict[ActuatorType, tuple[set[str], tuple[str, ...]]] = {
            "switch_group": (
                {"wizard-switch-group.ini", "wizard-phase1-switch.ini", "wizard-phase2-switch.ini", "wizard-phase3-switch.ini"},
                ("Type=switch_group", "SupportedPhaseSelections=P1,P1_P2", "BaseUrl=http://switch.local"),
            ),
            "template_switch": ({"wizard-switch.ini"}, ("Type=template_switch", "BaseUrl=http://switch.local", "Url=/wizard/switch/state")),
            "shelly_switch": ({"wizard-switch.ini"}, ("Type=shelly_switch", "Host=switch.local")),
            "shelly_contactor_switch": ({"wizard-switch.ini"}, ("Type=shelly_switch", "Host=switch.local")),
            "tuya_switch": ({"wizard-switch.ini"}, ("Type=tuya_switch", "BaseUrl=http://switch.local")),
            "tuya_contactor_switch": ({"wizard-switch.ini"}, ("Type=tuya_switch", "BaseUrl=http://switch.local")),
            "tasmota_switch": ({"wizard-switch.ini"}, ("Type=tasmota_switch", "BaseUrl=http://switch.local")),
            "tasmota_contactor_switch": ({"wizard-switch.ini"}, ("Type=tasmota_switch", "BaseUrl=http://switch.local")),
        }
        for actuator_type, (expected_files, expected_fragments) in cases.items():
            with self.subTest(actuator_type=actuator_type):
                files = render_adapter_files_from_topology(
                    _topology(
                        topology_type="hybrid_topology",
                        actuator=ActuatorConfig(type=actuator_type, config_path="switch.ini"),
                        charger=ChargerConfig(type="goe_charger"),
                    ),
                    _answers(),
                    {"charger": "charger.local", "switch": "switch.local"},
                )
                self.assertTrue(expected_files.issubset(files))
                combined_text = "\n".join(files.values())
                for fragment in expected_fragments:
                    self.assertIn(fragment, combined_text)

        fallback_files = render_adapter_files_from_topology(
            _topology(
                topology_type="hybrid_topology",
                actuator=ActuatorConfig(type="shelly_switch", config_path="switch.ini"),
                charger=ChargerConfig(type="goe_charger"),
            ),
            _answers(),
            {"charger": "charger.local"},
        )
        self.assertIn("Host=shared.local", fallback_files["wizard-switch.ini"])

    def test_hybrid_topology_renders_external_meter_file_with_exact_key_and_text(self) -> None:
        files = render_adapter_files_from_topology(
            _topology(
                topology_type="hybrid_topology",
                measurement=MeasurementConfig(type="external_meter"),
                actuator=ActuatorConfig(type="template_switch", config_path="switch.ini"),
                charger=ChargerConfig(type="goe_charger"),
            ),
            _answers(topology_preset="template-stack"),
            {"charger": "charger.local", "meter": "meter.local", "switch": "switch.local"},
        )
        self.assertEqual(set(files), {"wizard-charger.ini", "wizard-meter.ini", "wizard-switch.ini"})
        self.assertIn("Type=goe_charger", files["wizard-charger.ini"])
        self.assertIn("Type=template_meter", files["wizard-meter.ini"])
        self.assertIn("BaseUrl=http://meter.local", files["wizard-meter.ini"])

    def test_unsupported_actuator_does_not_hide_charger_file(self) -> None:
        files = render_adapter_files_from_topology(
            _topology(
                topology_type="hybrid_topology",
                actuator=ActuatorConfig(type="custom", config_path="switch.ini"),
                charger=ChargerConfig(type="goe_charger"),
            ),
            _answers(),
            {"charger": "charger.local", "switch": "switch.local"},
        )
        self.assertEqual(set(files), {"wizard-charger.ini"})


if __name__ == "__main__":
    unittest.main()
