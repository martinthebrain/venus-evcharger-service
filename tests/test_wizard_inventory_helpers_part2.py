# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_wizard_inventory_helpers_support import *  # noqa: F401,F403

class _WizardInventoryHelperTestsPart2:
    def test_guided_cli_support_helpers_cover_charger_and_skip_paths(self) -> None:
        charger_flags = _guided_capability_flags(_namespace(non_interactive=True), "charger", ("L1", "L2", "L3"))
        self.assertEqual(
            charger_flags,
            {
                "measures_power": False,
                "measures_energy": False,
                "switching_mode": None,
                "supports_feedback": False,
                "supports_phase_selection": False,
            },
        )

        inventory_with_group_default = DeviceInventory(
            profiles=_inventory_with_binding().profiles,
            devices=_inventory_with_binding().devices,
            bindings=_inventory_with_binding().bindings
            + (
                RoleBinding(
                    id="measurement_measurement",
                    role="measurement",
                    label="Measurement group",
                    phase_scope=("L1",),
                    members=(),
                ),
            ),
        )
        binding_id, existing_binding, binding_label, binding_scope = _prompt_binding_choice(
            _namespace(non_interactive=True),
            inventory_with_group_default,
            "measurement",
        )
        self.assertEqual(binding_id, "measurement_group")
        self.assertIsNone(existing_binding)
        self.assertEqual(binding_label, "Measurement")
        self.assertEqual(binding_scope, ("L1",))

        updated, device_id, binding_ref = _maybe_add_guided_device_and_binding(
            _namespace(non_interactive=True),
            _inventory_with_binding(),
            profile_id="meter_profile",
            label="Meter profile",
            capability_id="meter",
            supported_phases=("L1",),
            inferred_role="measurement",
        )
        self.assertEqual(updated, _inventory_with_binding())
        self.assertIsNone(device_id)
        self.assertIsNone(binding_ref)

        namespace = _namespace(
            non_interactive=True,
            _inventory_prompt_device=True,
            inventory_device_id="meter_l3",
            inventory_label="Meter L3",
        )
        updated, device_id, binding_ref = _maybe_add_guided_device_and_binding(
            namespace,
            _inventory_with_binding(),
            profile_id="meter_profile",
            label="Meter profile",
            capability_id="meter",
            supported_phases=("L1",),
            inferred_role="measurement",
        )
        self.assertEqual(device_id, "meter_l3")
        self.assertIsNone(binding_ref)

        inventory = _inventory_with_binding()
        inventory = DeviceInventory(
            profiles=inventory.profiles,
            devices=(inventory.devices[0],),
        )
        namespace = _namespace(
            inventory_binding_role="measurement",
            inventory_binding_id="measurement_group",
            inventory_binding_label="Measurement group",
            inventory_binding_phase_scope="L1,L2",
        )
        with (
            patch("builtins.input", side_effect=["1", "L1"]),
            patch("venus_evcharger.bootstrap.wizard_inventory_prompts.prompt_yes_no", return_value=False),
        ):
            with self.assertRaisesRegex(ValueError, "do not match the requested binding phase scope"):
                guided_inventory_edit_binding(namespace, Path("/tmp/inventory.ini"), inventory)

    def test_set_inventory_binding_member_replaces_existing_member_and_infers_new_binding(self) -> None:
        inventory = _inventory_with_binding()
        replaced = set_inventory_binding_member(
            inventory,
            binding_id="measurement",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L3",),
        )
        self.assertEqual(replaced.bindings[0].members[0].phases, ("L3",))
        created = set_inventory_binding_member(
            inventory,
            binding_id="new_measurement",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L1",),
        )
        self.assertEqual(created.bindings[-1].role, "measurement")
        self.assertEqual(created.bindings[-1].label, "New Measurement")

    def test_remove_inventory_binding_member_covers_missing_and_empty_binding_paths(self) -> None:
        inventory = _inventory_with_binding()
        updated = remove_inventory_binding_member(inventory, binding_id="measurement", device_id="meter_l2")
        self.assertEqual(len(updated.bindings[0].members), 1)
        removed_all = remove_inventory_binding_member(updated, binding_id="measurement", device_id="meter_l1")
        self.assertEqual(removed_all.bindings, ())
        with self.assertRaisesRegex(ValueError, "Unknown binding id"):
            remove_inventory_binding_member(inventory, binding_id="missing", device_id="meter_l1")
        with self.assertRaisesRegex(ValueError, "has no member"):
            remove_inventory_binding_member(inventory, binding_id="measurement", device_id="missing")

    def test_run_inventory_editor_guided_actions_reject_non_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "inventory.ini"
            parser = configparser.ConfigParser()
            parser.read_string(
                """
[Profile:meter_profile]
Label=Meter profile

[Capability:meter_profile:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
MeasuresEnergy=1
"""
            )
            inventory_path.write_text(parser_to_text(parser), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "guided-add-profile requires interactive input"):
                run_inventory_editor(
                    _namespace(
                        inventory_action="guided-add-profile",
                        inventory_path=str(inventory_path),
                        non_interactive=True,
                    )
                )
            with self.assertRaisesRegex(ValueError, "guided-edit-binding requires interactive input"):
                run_inventory_editor(
                    _namespace(
                        inventory_action="guided-edit-binding",
                        inventory_path=str(inventory_path),
                        non_interactive=True,
                    )
                )

    def test_run_inventory_editor_guided_edit_binding_requires_eligible_choices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "inventory.ini"
            inventory_path.write_text(
                """
[Profile:meter_profile]
Label=Meter profile

[Capability:meter_profile:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
MeasuresEnergy=1
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "No eligible devices"):
                run_inventory_editor(
                    _namespace(
                        inventory_action="guided-edit-binding",
                        inventory_path=str(inventory_path),
                        inventory_binding_role="measurement",
                    )
                )

    def test_build_wizard_inventory_covers_switch_group_and_native_measurement(self) -> None:
        answers = WizardAnswers(
            profile="multi_adapter_topology",
            host_input="switch.local",
            meter_host_input=None,
            switch_host_input="switch.local",
            charger_host_input=None,
            device_instance=1,
            phase="3P",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            topology_preset="goe-external-switch-group",
            charger_backend=None,
            switch_group_supported_phase_selections="P1,P1_P2_P3",
        )
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="switch_group"),
            measurement=MeasurementConfig(type="actuator_native"),
            policy=PolicyConfig(mode="manual", phase="3P"),
        )
        inventory = build_wizard_inventory(answers, {"switch": "switch.local"}, topology)
        self.assertEqual(len(inventory.devices), 3)
        self.assertEqual(inventory.bindings[0].role, "actuation")
        self.assertEqual(inventory.bindings[1].role, "measurement")
        self.assertEqual(inventory.bindings[1].phase_scope, ("L1", "L2", "L3"))

    def test_build_wizard_inventory_covers_fixed_reference_and_charger_native(self) -> None:
        answers = WizardAnswers(
            profile="native_device",
            host_input="charger.local",
            meter_host_input=None,
            switch_host_input=None,
            charger_host_input="charger.local",
            device_instance=2,
            phase="L2",
            policy_mode="auto",
            digest_auth=False,
            username="",
            password="",
            charger_backend="goe_charger",
            topology_preset=None,
        )
        fixed_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            measurement=MeasurementConfig(type="fixed_reference"),
            policy=PolicyConfig(mode="manual", phase="L2"),
        )
        fixed_inventory = build_wizard_inventory(answers, {"meter": "meter.local"}, fixed_topology)
        self.assertEqual(fixed_inventory.devices[0].endpoint, "meter.local")
        self.assertFalse(fixed_inventory.profiles[0].capabilities[0].measures_energy)

        charger_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(type="goe_charger"),
            policy=PolicyConfig(mode="auto", phase="L2"),
        )
        charger_inventory = build_wizard_inventory(answers, {"charger": "charger.local"}, charger_topology)
        payload = inventory_payload(charger_inventory)
        rendered = inventory_text(answers, {"charger": "charger.local"}, charger_topology)
        self.assertEqual(payload["bindings"][0]["role"], "measurement")
        self.assertIn("[Binding:charger]", rendered)

    def test_build_wizard_inventory_ignores_unknown_measurement_type(self) -> None:
        answers = WizardAnswers(
            profile="native_device",
            host_input="charger.local",
            meter_host_input=None,
            switch_host_input=None,
            charger_host_input="charger.local",
            device_instance=1,
            phase="L1",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            charger_backend="goe_charger",
            topology_preset=None,
        )
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            measurement=cast(MeasurementConfig, argparse.Namespace(type="mystery")),
            charger=ChargerConfig(type="goe_charger"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )

        inventory = build_wizard_inventory(answers, {"charger": "charger.local"}, topology)

        self.assertEqual(len(inventory.bindings), 1)
        self.assertEqual(inventory.bindings[0].role, "charger")

    def test_inventory_cli_and_runtime_result_helpers_cover_remaining_edge_branches(self) -> None:
        binding = _inventory_with_binding().bindings[0]
        self.assertEqual(_binding_label_default(None, "measurement"), "Measurement")
        self.assertEqual(_binding_label_default(binding, "measurement"), "Measurement")
        self.assertEqual(_binding_scope_default(None), "L1")
        self.assertEqual(_binding_scope_default(binding), "L1,L2")

        switch_flags = _guided_capability_flags(
            _namespace(
                non_interactive=True,
                inventory_supports_feedback=True,
                inventory_supports_phase_selection=True,
            ),
            "switch",
            ("L1", "L2"),
        )
        self.assertEqual(switch_flags["switching_mode"], "contactor")
        self.assertTrue(switch_flags["supports_feedback"])
        self.assertTrue(switch_flags["supports_phase_selection"])

        inventory = _inventory_with_binding()
        untouched, existing_binding = _maybe_replace_binding(
            _namespace(non_interactive=True, _inventory_replace_binding=False),
            inventory,
            inventory.bindings[0],
            "measurement",
        )
        self.assertEqual(untouched.bindings[0].id, "measurement")
        self.assertIsNotNone(existing_binding)

        replaced, replaced_binding = _maybe_replace_binding(
            _namespace(non_interactive=True, _inventory_replace_binding=True),
            inventory,
            inventory.bindings[0],
            "measurement",
        )
        self.assertEqual(replaced.bindings, ())
        self.assertIsNone(replaced_binding)

        with self.assertRaisesRegex(ValueError, "was not created"):
            _validated_guided_binding(DeviceInventory(), "measurement", ("L1",))
        with self.assertRaisesRegex(ValueError, "do not match"):
            _validated_guided_binding(inventory, "measurement", ("L1",))

        with self.assertRaisesRegex(ValueError, "requires interactive input"):
            guided_inventory_add_profile(_namespace(non_interactive=True), Path("/tmp/inventory.ini"), DeviceInventory())

        with self.assertRaisesRegex(ValueError, "Unsupported inventory action"):
            run_simple_inventory_action("unsupported", _namespace(), Path("/tmp/inventory.ini"), DeviceInventory())

        ready = json_ready({"path": Path("/tmp/device.ini"), "items": (Path("/tmp/child.ini"), {"nested": [Path("/tmp/x.ini")]})})
        self.assertEqual(
            ready,
            {"path": "/tmp/device.ini", "items": ["/tmp/child.ini", {"nested": ["/tmp/x.ini"]}]},
        )
        answer_ready = json_ready(
            WizardAnswers(
                profile="simple_relay",
                host_input="switch.local",
                meter_host_input=None,
                switch_host_input="switch.local",
                charger_host_input=None,
                device_instance=1,
                phase="L1",
                policy_mode="manual",
                digest_auth=False,
                username="",
                password="",
            )
        )
        self.assertIsInstance(answer_ready, dict)
        self.assertEqual(answer_ready["profile"], "simple_relay")
        self.assertIs(json_ready(WizardAnswers), WizardAnswers)
