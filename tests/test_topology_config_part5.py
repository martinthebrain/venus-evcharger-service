# SPDX-License-Identifier: GPL-3.0-or-later
from typing import cast

from tests.test_topology_config_support import *  # noqa: F401,F403


class _TopologyConfigTestsPart5:
    class _LegacyCaseSensitiveConfig:
        def __init__(
            self,
            *,
            defaults: dict[str, object] | None = None,
            backends: dict[str, object] | None = None,
        ) -> None:
            self._defaults = defaults
            self._backends = backends

        def __contains__(self, key: str) -> bool:
            return key == "DEFAULT" and self._defaults is not None

        def __getitem__(self, key: str) -> dict[str, object]:
            if key == "DEFAULT" and self._defaults is not None:
                return self._defaults
            if key == "Backends" and self._backends is not None:
                return self._backends
            raise KeyError(key)

        def has_section(self, name: str) -> bool:
            return name == "Backends" and self._backends is not None

    def test_legacy_runtime_defaults_are_explicit_without_sections(self) -> None:
        runtime = _legacy_runtime_values(configparser.ConfigParser())

        self.assertEqual(runtime.defaults, {})
        self.assertEqual(runtime.host, "")
        self.assertEqual(runtime.meter_type, "shelly_meter")
        self.assertEqual(runtime.switch_type, "shelly_contactor_switch")
        self.assertEqual(runtime.charger_type_raw, "")
        self.assertIsNone(runtime.meter_path)
        self.assertIsNone(runtime.switch_path)
        self.assertIsNone(runtime.charger_path)

    def test_legacy_topology_defaults_keep_manual_simple_relay_shape(self) -> None:
        parsed = legacy_topology_from_config(configparser.ConfigParser())

        self.assertEqual(parsed.topology.type, "simple_relay")
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")
        self.assertIsNone(parsed.actuator.config_path)
        self.assertEqual(parsed.measurement.type, "none")
        self.assertIsNone(parsed.charger)
        self.assertEqual(parsed.policy.mode, "manual")
        self.assertEqual(parsed.policy.phase, "L1")

    def test_legacy_topology_uses_host_as_actuator_native_measurement_source(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.178.76
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "simple_relay")
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")
        self.assertEqual(parsed.measurement.type, "actuator_native")
        self.assertEqual(parsed.policy.mode, "manual")
        self.assertEqual(parsed.policy.phase, "L1")

    def test_legacy_topology_keeps_native_charger_meter_fallbacks_distinct(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=none
ChargerType=goe_charger
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "native_device")
        self.assertIsNone(parsed.actuator)
        self.assertEqual(parsed.measurement.type, "charger_native")
        self.assertIsNone(parsed.measurement.config_path)
        self.assertEqual(parsed.charger.type, "goe_charger")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=none
ChargerType=goe_charger
MeterConfigPath=/data/etc/meter.ini
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "native_device")
        self.assertEqual(parsed.measurement.type, "external_meter")
        self.assertEqual(parsed.measurement.config_path, "/data/etc/meter.ini")

    def test_legacy_topology_keeps_hybrid_meter_fallbacks_distinct(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=template_switch
ChargerType=goe_charger
MeterType=none
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "hybrid_topology")
        self.assertEqual(parsed.actuator.type, "template_switch")
        self.assertEqual(parsed.measurement.type, "charger_native")
        self.assertEqual(parsed.charger.type, "goe_charger")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=template_switch
ChargerType=goe_charger
MeterConfigPath=/data/etc/meter.ini
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "hybrid_topology")
        self.assertEqual(parsed.measurement.type, "external_meter")
        self.assertEqual(parsed.measurement.config_path, "/data/etc/meter.ini")

    def test_legacy_runtime_uses_exact_field_names_at_mapping_boundary(self) -> None:
        config = self._LegacyCaseSensitiveConfig(
            defaults={"Host": " direct.local "},
            backends={
                "MeterType": " TEMPLATE_METER ",
                "SwitchType": " TEMPLATE_SWITCH ",
                "ChargerType": " GOE_CHARGER ",
                "MeterConfigPath": " /data/etc/meter.ini ",
                "SwitchConfigPath": " /data/etc/switch.ini ",
                "ChargerConfigPath": " /data/etc/charger.ini ",
            },
        )

        runtime = _legacy_runtime_values(cast(configparser.ConfigParser, config))

        self.assertEqual(runtime.host, "direct.local")
        self.assertEqual(runtime.meter_type, "template_meter")
        self.assertEqual(runtime.switch_type, "template_switch")
        self.assertEqual(runtime.charger_type_raw, "goe_charger")
        self.assertEqual(runtime.meter_path, "/data/etc/meter.ini")
        self.assertEqual(runtime.switch_path, "/data/etc/switch.ini")
        self.assertEqual(runtime.charger_path, "/data/etc/charger.ini")

    def test_runtime_summary_loader_uses_exact_legacy_field_names_at_mapping_boundary(self) -> None:
        config = self._LegacyCaseSensitiveConfig(
            defaults={"Host": " direct.local "},
            backends={
                "Mode": " split ",
                "MeterType": " TEMPLATE_METER ",
                "SwitchType": " TEMPLATE_SWITCH ",
                "ChargerType": " GOE_CHARGER ",
                "MeterConfigPath": " /data/etc/meter.ini ",
                "SwitchConfigPath": " /data/etc/switch.ini ",
                "ChargerConfigPath": " /data/etc/charger.ini ",
            },
        )

        summary = load_runtime_backend_summary(cast(configparser.ConfigParser, config))

        self.assertEqual(summary.backend_mode, "split")
        self.assertEqual(summary.meter_type, "template_meter")
        self.assertEqual(str(summary.meter_config_path), "/data/etc/meter.ini")
        self.assertEqual(summary.switch_type, "template_switch")
        self.assertEqual(str(summary.switch_config_path), "/data/etc/switch.ini")
        self.assertEqual(summary.charger_type, "goe_charger")
        self.assertEqual(str(summary.charger_config_path), "/data/etc/charger.ini")
        self.assertTrue(summary.topology_configured)
        self.assertFalse(summary.primary_rpc_configured)

    def test_runtime_summary_loader_preserves_legacy_combined_defaults_and_host_state(self) -> None:
        config = self._LegacyCaseSensitiveConfig(defaults={"Host": " direct.local "})

        summary = load_runtime_backend_summary(cast(configparser.ConfigParser, config))

        self.assertEqual(summary.backend_mode, "combined")
        self.assertEqual(summary.meter_type, "shelly_meter")
        self.assertIsNone(summary.meter_config_path)
        self.assertEqual(summary.switch_type, "shelly_contactor_switch")
        self.assertIsNone(summary.switch_config_path)
        self.assertIsNone(summary.charger_type)
        self.assertIsNone(summary.charger_config_path)
        self.assertTrue(summary.topology_configured)
        self.assertTrue(summary.primary_rpc_configured)

    def test_runtime_summary_loader_uses_default_section_as_legacy_backend_fallback(self) -> None:
        config = self._LegacyCaseSensitiveConfig(
            defaults={
                "Mode": " split ",
                "MeterType": " none ",
                "SwitchType": " none ",
                "ChargerType": " GOE_CHARGER ",
                "ChargerConfigPath": " /data/etc/charger.ini ",
            },
        )

        summary = load_runtime_backend_summary(cast(configparser.ConfigParser, config))

        self.assertEqual(summary.backend_mode, "split")
        self.assertIsNone(summary.meter_type)
        self.assertIsNone(summary.meter_config_path)
        self.assertIsNone(summary.switch_type)
        self.assertIsNone(summary.switch_config_path)
        self.assertEqual(summary.charger_type, "goe_charger")
        self.assertEqual(str(summary.charger_config_path), "/data/etc/charger.ini")
        self.assertTrue(summary.topology_configured)
        self.assertFalse(summary.primary_rpc_configured)

    def test_runtime_summary_loader_expands_combined_aliases_by_runtime_role(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
Mode=split
MeterType=shelly_combined
SwitchType=shelly_combined
ChargerType=goe_charger
"""
        )

        summary = load_runtime_backend_summary(parser)

        self.assertEqual(summary.backend_mode, "split")
        self.assertEqual(summary.meter_type, "shelly_meter")
        self.assertEqual(summary.switch_type, "shelly_contactor_switch")
        self.assertEqual(summary.charger_type, "goe_charger")

    def test_runtime_summary_loader_keeps_split_none_roles_distinct_from_missing_paths(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
Mode=split
MeterType=none
SwitchType=none
ChargerType=goe_charger
"""
        )

        summary = load_runtime_backend_summary(parser)

        self.assertEqual(summary.backend_mode, "split")
        self.assertIsNone(summary.meter_type)
        self.assertIsNone(summary.meter_config_path)
        self.assertIsNone(summary.switch_type)
        self.assertIsNone(summary.switch_config_path)
        self.assertEqual(summary.charger_type, "goe_charger")
        self.assertIsNone(summary.charger_config_path)
        self.assertFalse(summary.topology_configured)
        self.assertFalse(summary.primary_rpc_configured)

    def test_runtime_summary_loader_keeps_combined_explicit_roles_on_primary_rpc_host(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.178.76

[Backends]
MeterType=template_meter
SwitchType=template_switch
"""
        )

        summary = load_runtime_backend_summary(parser)

        self.assertEqual(summary.backend_mode, "combined")
        self.assertEqual(summary.meter_type, "template_meter")
        self.assertEqual(summary.switch_type, "template_switch")
        self.assertIsNone(summary.charger_type)
        self.assertTrue(summary.topology_configured)
        self.assertTrue(summary.primary_rpc_configured)

    def test_runtime_summary_loader_rejects_none_roles_without_split_charger_contract(self) -> None:
        invalid_meter_mode = configparser.ConfigParser()
        invalid_meter_mode.read_string(
            """
[Backends]
Mode=combined
MeterType=none
ChargerType=goe_charger
"""
        )
        with self.assertRaisesRegex(ValueError, "MeterType=none is only supported in split backend mode"):
            load_runtime_backend_summary(invalid_meter_mode)

        invalid_meter_charger = configparser.ConfigParser()
        invalid_meter_charger.read_string(
            """
[Backends]
Mode=split
MeterType=none
ChargerType=
"""
        )
        with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
            load_runtime_backend_summary(invalid_meter_charger)

        invalid_switch_charger = configparser.ConfigParser()
        invalid_switch_charger.read_string(
            """
[Backends]
Mode=split
SwitchType=none
ChargerType=
"""
        )
        with self.assertRaisesRegex(ValueError, "SwitchType=none requires a configured charger backend"):
            load_runtime_backend_summary(invalid_switch_charger)

    def test_runtime_summary_builder_derives_configured_flags_from_each_runtime_role(self) -> None:
        combined_without_host = _build_runtime_summary(
            backend_mode="combined",
            meter_type="shelly_meter",
            meter_config_path=None,
            switch_type="shelly_contactor_switch",
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
        )
        self.assertFalse(combined_without_host.topology_configured)
        self.assertFalse(combined_without_host.primary_rpc_configured)

        meter_only = _build_runtime_summary(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=Path("/data/etc/meter.ini"),
            switch_type=None,
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
        )
        self.assertTrue(meter_only.topology_configured)

        switch_only = _build_runtime_summary(
            backend_mode="split",
            meter_type=None,
            meter_config_path=None,
            switch_type="template_switch",
            switch_config_path=Path("/data/etc/switch.ini"),
            charger_type=None,
            charger_config_path=None,
        )
        self.assertTrue(switch_only.topology_configured)

        unpaired_roles = _build_runtime_summary(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=None,
            switch_type=None,
            switch_config_path=Path("/data/etc/switch.ini"),
            charger_type="goe_charger",
            charger_config_path=None,
        )
        self.assertFalse(unpaired_roles.topology_configured)

    def test_runtime_summary_public_predicates_do_not_let_legacy_host_override_split_state(self) -> None:
        split_unconfigured = BackendRuntimeSummary(
            backend_mode="split",
            meter_type=None,
            meter_config_path=None,
            switch_type=None,
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            topology_configured=False,
            primary_rpc_configured=False,
        )

        self.assertFalse(runtime_summary_is_configured(split_unconfigured))
        self.assertFalse(runtime_summary_is_configured(split_unconfigured, legacy_host="192.168.178.76"))
        self.assertFalse(runtime_summary_uses_legacy_primary_rpc(split_unconfigured, legacy_host="192.168.178.76"))

        split_primary_rpc = BackendRuntimeSummary(
            backend_mode="split",
            meter_type=None,
            meter_config_path=None,
            switch_type=None,
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            topology_configured=False,
            primary_rpc_configured=True,
        )
        self.assertTrue(runtime_summary_uses_legacy_primary_rpc(split_primary_rpc))

        combined_unconfigured = BackendRuntimeSummary(
            backend_mode="combined",
            meter_type="shelly_meter",
            meter_config_path=None,
            switch_type="shelly_contactor_switch",
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            topology_configured=False,
            primary_rpc_configured=False,
        )
        self.assertFalse(runtime_summary_is_configured(combined_unconfigured))
        self.assertFalse(runtime_summary_uses_legacy_primary_rpc(combined_unconfigured))

    def test_backend_normalization_contracts_keep_none_empty_and_legacy_view_fallbacks_explicit(self) -> None:
        self.assertEqual(_normalized_text_or_default(None), "")
        self.assertEqual(_normalized_text_or_default("  ", "fallback"), "fallback")
        self.assertEqual(_configured_text(None), "")
        self.assertEqual(normalize_backend_type(None, "fallback_backend"), "fallback_backend")

        self.assertEqual(_runtime_meter_role_from_legacy("combined", None), "shelly_meter")
        self.assertEqual(_runtime_switch_role_from_legacy("combined", None), "shelly_contactor_switch")

    def test_legacy_policy_uses_exact_default_field_names_at_mapping_boundary(self) -> None:
        config = self._LegacyCaseSensitiveConfig(defaults={"Mode": "2", "Phase": "L3"})

        parsed = legacy_topology_from_config(cast(configparser.ConfigParser, config))

        self.assertEqual(parsed.policy.mode, "scheduled")
        self.assertEqual(parsed.policy.phase, "L3")

    def test_legacy_hybrid_alias_keeps_host_context_for_switch_mapping(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.178.76

[Backends]
SwitchType=shelly_combined
ChargerType=goe_charger
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "hybrid_topology")
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")

    def test_runtime_summary_from_service_uses_bootstrap_summary_as_contract_boundary(self) -> None:
        service = SimpleNamespace(
            _backend_runtime_summary=runtime_summary_fixture(
                meter_type="shelly_meter",
                switch_type="template_switch",
                meter_config_path=Path("/data/etc/meter.ini"),
                switch_config_path=Path("/data/etc/switch.ini"),
                charger_config_path=Path("/data/etc/charger.ini"),
            )
        )

        summary = runtime_summary_from_service(service)

        self.assertEqual(summary.backend_mode, "split")
        self.assertEqual(summary.meter_type, "shelly_meter")
        self.assertEqual(str(summary.meter_config_path), "/data/etc/meter.ini")
        self.assertEqual(summary.switch_type, "template_switch")
        self.assertEqual(str(summary.switch_config_path), "/data/etc/switch.ini")
        self.assertEqual(summary.charger_type, "goe_charger")
        self.assertEqual(str(summary.charger_config_path), "/data/etc/charger.ini")
        self.assertTrue(summary.topology_configured)
        self.assertFalse(summary.primary_rpc_configured)

    def test_backend_service_label_helpers_use_defaults_without_canonical_state(self) -> None:
        empty_service = SimpleNamespace()
        self.assertEqual(backend_mode_for_service(empty_service), "combined")
        self.assertEqual(backend_mode_for_service(empty_service, "fallback"), "fallback")
        self.assertEqual(backend_type_for_service(empty_service, "meter", "fallback"), "fallback")
        self.assertEqual(backend_type_for_service(empty_service, "meter"), "")
        configured_service = SimpleNamespace(
            _backend_runtime_summary=runtime_summary_fixture(),
        )
        self.assertEqual(backend_mode_for_service(configured_service), "split")
        self.assertEqual(backend_type_for_service(configured_service, "meter", "fallback"), "template_meter")
        self.assertEqual(backend_type_for_service(configured_service, "switch", "fallback"), "template_switch")
        self.assertEqual(backend_type_for_service(configured_service, "charger", "fallback"), "goe_charger")

    def test_backend_service_label_helpers_normalize_roles_and_empty_runtime_labels(self) -> None:
        runtime = BackendRuntimeSummary(
            backend_mode="split",
            meter_type="",
            meter_config_path=None,
            switch_type="template_switch",
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            topology_configured=False,
            primary_rpc_configured=False,
        )
        service = SimpleNamespace(_backend_bundle=SimpleNamespace(runtime=runtime))

        self.assertEqual(backend_mode_for_service(service), "split")
        self.assertEqual(backend_type_for_service(service, " METER ", "fallback"), "fallback")
        self.assertEqual(backend_type_for_service(service, " SWITCH ", "fallback"), "template_switch")
        self.assertEqual(backend_type_for_service(service, " charger ", "fallback"), "fallback")
        self.assertEqual(backend_type_for_service(service, "unknown", "fallback"), "fallback")

        missing_mode_service = SimpleNamespace(_backend_bundle=SimpleNamespace(runtime=SimpleNamespace()))
        self.assertEqual(backend_mode_for_service(missing_mode_service, "fallback"), "fallback")
        empty_mode_service = SimpleNamespace(_backend_bundle=SimpleNamespace(runtime=SimpleNamespace(backend_mode="")))
        self.assertEqual(backend_mode_for_service(empty_mode_service, "fallback"), "fallback")

    def test_backend_service_label_helpers_use_legacy_config_summary_when_no_topology_sections_exist(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
Mode=split
MeterType=template_meter
SwitchType=template_switch
ChargerType=goe_charger
"""
        )
        service = SimpleNamespace(config=parser)

        self.assertEqual(backend_mode_for_service(service), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "fallback"), "template_meter")
        self.assertEqual(backend_type_for_service(service, "switch", "fallback"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", "fallback"), "goe_charger")

    def test_backend_selection_view_maps_combined_defaults_exactly(self) -> None:
        view = backend_selection_view(
            BackendRuntimeSummary(
                backend_mode="combined",
                meter_type=None,
                meter_config_path=None,
                switch_type=None,
                switch_config_path=None,
                charger_type=None,
                charger_config_path=None,
                topology_configured=False,
                primary_rpc_configured=False,
            )
        )

        self.assertEqual(
            view,
            {
                "mode": "combined",
                "meter_type": "shelly_meter",
                "switch_type": "shelly_contactor_switch",
                "charger_type": None,
                "meter_config_path": None,
                "switch_config_path": None,
                "charger_config_path": None,
            },
        )

    def test_backend_selection_view_maps_split_roles_and_paths_exactly(self) -> None:
        view = backend_selection_view(
            BackendRuntimeSummary(
                backend_mode="split",
                meter_type=None,
                meter_config_path=Path("/data/etc/meter.ini"),
                switch_type=None,
                switch_config_path=Path("/data/etc/switch.ini"),
                charger_type="goe_charger",
                charger_config_path=Path("/data/etc/charger.ini"),
                topology_configured=True,
                primary_rpc_configured=False,
            )
        )

        self.assertEqual(view["mode"], "split")
        self.assertEqual(view["meter_type"], "none")
        self.assertEqual(str(view["meter_config_path"]), "/data/etc/meter.ini")
        self.assertEqual(view["switch_type"], "none")
        self.assertEqual(str(view["switch_config_path"]), "/data/etc/switch.ini")
        self.assertEqual(view["charger_type"], "goe_charger")
        self.assertEqual(str(view["charger_config_path"]), "/data/etc/charger.ini")

    def test_backend_selection_view_rejects_non_runtime_objects(self) -> None:
        with self.assertRaisesRegex(TypeError, "BackendRuntimeSummary"):
            backend_selection_view(cast(Any, None))
        with self.assertRaisesRegex(TypeError, "BackendRuntimeSummary"):
            backend_selection_view(cast(Any, SimpleNamespace(meter_type="template_meter")))

    def test_backend_selection_view_maps_empty_canonical_roles(self) -> None:
        view = backend_selection_view(
            BackendRuntimeSummary(
                backend_mode="split",
                meter_type=None,
                meter_config_path=None,
                switch_type=None,
                switch_config_path=None,
                charger_type=None,
                charger_config_path=None,
                topology_configured=False,
                primary_rpc_configured=False,
            )
        )

        self.assertEqual(view["mode"], "split")
        self.assertEqual(view["meter_type"], "none")
        self.assertEqual(view["switch_type"], "none")
        self.assertIsNone(view["charger_type"])
        self.assertIsNone(view["meter_config_path"])
        self.assertIsNone(view["switch_config_path"])
        self.assertIsNone(view["charger_config_path"])

    def test_runtime_summary_from_service_accepts_canonical_none_roles(self) -> None:
        allowed_none_roles = runtime_summary_from_service(
            SimpleNamespace(
                _backend_runtime_summary=runtime_summary_fixture(
                    meter_type=None,
                    switch_type=None,
                )
            )
        )
        self.assertEqual(allowed_none_roles.backend_mode, "split")
        self.assertIsNone(allowed_none_roles.meter_type)
        self.assertIsNone(allowed_none_roles.switch_type)
        self.assertEqual(allowed_none_roles.charger_type, "goe_charger")
