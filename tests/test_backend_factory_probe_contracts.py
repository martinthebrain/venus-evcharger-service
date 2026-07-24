# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for backend factory, registry, and probe boundaries."""

from __future__ import annotations

import configparser
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_snapshot
from venus_evcharger.backend import factory, probe, registry
from venus_evcharger.backend.factory_contracts import (
    config_from_backend_service,
    is_backend_config_source,
    is_backend_topology_source,
    topology_from_backend_service,
)
from venus_evcharger.backend.models import BackendRuntimeSummary
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsUnavailable
from venus_evcharger.topology.config import parse_topology_config
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    TopologyConfig,
)


class _FakeBackend:
    def __init__(self, service: object, *, config_path: str = "") -> None:
        if service is None:
            raise AssertionError("probe backend constructors require a service object")
        self.service = service
        self.config_path = config_path


class _FakeMeterBackend(_FakeBackend):
    settings = SimpleNamespace(profile_name="fake-meter-profile", component="EM", device_id=9)

    def read_meter(self) -> dict[str, object]:
        return {"power_w": 1234.0, "nested": {"path": Path("/tmp/meter")}}


class _FakeSwitchBackend(_FakeBackend):
    settings = SimpleNamespace(
        profile_name="fake-switch-profile",
        component="Switch",
        device_id=4,
        phase_switch_targets={"P1": ("relay-1",)},
        phase_members={"P1": {"backend_type": "fake-switch"}},
        feedback_readback={"component": "Input", "device_id": 1},
        interlock_readback={"component": "Input", "device_id": 2},
    )

    def capabilities(self) -> dict[str, object]:
        return {"switching_mode": "contactor", "supported_phase_selections": ("P1", "P1_P2_P3")}

    def read_switch_state(self) -> dict[str, object]:
        return {"enabled": True, "phase_selection": "P1"}


class _FakeChargerBackend(_FakeBackend):
    settings = SimpleNamespace(
        profile_name="fake-charger-profile",
        transport_settings=SimpleNamespace(
            transport_kind="tcp",
            unit_id=8,
            device="/dev/ttyS1",
            timeout_seconds=3.5,
            serial_port_owner="venus_serial_starter",
            serial_retry_count=2,
            serial_retry_delay_seconds=0.25,
        ),
        supported_phase_selections=("P1", "P1_P2"),
        state_url="/state",
        state_actual_current_path="actual_current",
        state_power_watts_path="power",
        state_energy_kwh_path="energy",
        state_status_path="status",
        state_fault_path="fault",
        enable_url="/enable",
        current_url="/current",
        phase_url="/phase",
    )

    def read_charger_state(self) -> dict[str, object]:
        return {"enabled": True, "current_amps": 12.0, "phase_selection": "P1_P2"}


class _MinimalSwitchBackend(_FakeBackend):
    settings = SimpleNamespace()

    def capabilities(self) -> dict[str, object]:
        return {}

    def read_switch_state(self) -> dict[str, object]:
        return {}


class _MinimalChargerBackend(_FakeBackend):
    settings = SimpleNamespace()

    def read_charger_state(self) -> dict[str, object]:
        return {}


class _NoSettingsMeterBackend(_FakeBackend):
    def read_meter(self) -> dict[str, object]:
        return {}


class _NoSettingsSwitchBackend(_FakeBackend):
    def capabilities(self) -> dict[str, object]:
        return {}

    def read_switch_state(self) -> dict[str, object]:
        return {}


class _NoSettingsChargerBackend(_FakeBackend):
    def read_charger_state(self) -> dict[str, object]:
        return {}


class _GatewayDiagnosticsReader:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.snapshot = gateway_diagnostics_snapshot()
        self.unavailable = unavailable

    def read_snapshot(self):
        if self.unavailable:
            raise GatewayDiagnosticsUnavailable("offline")
        return self.snapshot


def _parser_from_text(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return parser


def _write_config(directory: str, name: str, content: str) -> str:
    path = Path(directory) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestBackendRegistryContracts(unittest.TestCase):
    def test_switch_group_registry_injects_the_registry_owned_child_factory(self) -> None:
        service = object()
        expected_backend = object()
        with patch.object(registry, "SwitchGroupBackend", return_value=expected_backend) as group_constructor:
            backend = registry.create_switch_backend("switch_group", service, "/tmp/group.ini")

        self.assertIs(backend, expected_backend)
        group_constructor.assert_called_once_with(
            service,
            config_path="/tmp/group.ini",
            child_backend_factory=registry._create_switch_group_child_backend,
        )

    def test_switch_group_child_factory_keeps_its_role_specific_unknown_type_error(self) -> None:
        with patch.dict(registry.SWITCH_BACKENDS, {}, clear=True):
            with self.assertRaisesRegex(
                ValueError,
                "^Unsupported switch-group child backend 'missing'$",
            ):
                registry._create_switch_group_child_backend("missing", object(), "/tmp/child.ini")

    def test_create_meter_backend_normalizes_type_and_passes_config_path(self) -> None:
        service = object()
        with (
            patch.dict(registry.METER_BACKENDS, {"fake_meter": _FakeBackend}, clear=True),
            patch.dict(registry.SWITCH_BACKENDS, {}, clear=True),
            patch.dict(registry.CHARGER_BACKENDS, {}, clear=True),
        ):
            backend = registry.create_meter_backend(" Fake_Meter ", service, "/tmp/meter.ini")

        self.assertIsInstance(backend, _FakeBackend)
        self.assertIs(backend.service, service)
        self.assertEqual(backend.config_path, "/tmp/meter.ini")

    def test_create_switch_backend_uses_switch_registry_only(self) -> None:
        service = object()
        with (
            patch.dict(registry.METER_BACKENDS, {}, clear=True),
            patch.dict(registry.SWITCH_BACKENDS, {"fake_switch": _FakeBackend}, clear=True),
            patch.dict(registry.CHARGER_BACKENDS, {}, clear=True),
        ):
            backend = registry.create_switch_backend("fake_switch", service, "/tmp/switch.ini")

        self.assertIsInstance(backend, _FakeBackend)
        self.assertIs(backend.service, service)
        self.assertEqual(backend.config_path, "/tmp/switch.ini")

    def test_create_charger_backend_uses_charger_registry_only(self) -> None:
        service = object()
        with (
            patch.dict(registry.METER_BACKENDS, {}, clear=True),
            patch.dict(registry.SWITCH_BACKENDS, {}, clear=True),
            patch.dict(registry.CHARGER_BACKENDS, {"fake_charger": _FakeBackend}, clear=True),
        ):
            backend = registry.create_charger_backend("fake_charger", service, "/tmp/charger.ini")

        self.assertIsInstance(backend, _FakeBackend)
        self.assertIs(backend.service, service)
        self.assertEqual(backend.config_path, "/tmp/charger.ini")

    def test_create_switch_and_charger_backend_reject_unknown_types_with_role_specific_errors(self) -> None:
        service = object()
        with self.assertRaisesRegex(ValueError, "Unsupported switch backend 'unknown_switch'"):
            registry.create_switch_backend("unknown_switch", service, "")
        with self.assertRaisesRegex(ValueError, "Unsupported charger backend 'unknown_charger'"):
            registry.create_charger_backend("unknown_charger", service, "")

    def test_backend_registry_wrappers_default_to_empty_config_path(self) -> None:
        service = object()
        with (
            patch.dict(registry.METER_BACKENDS, {"fake_meter": _FakeBackend}, clear=True),
            patch.dict(registry.SWITCH_BACKENDS, {"fake_switch": _FakeBackend}, clear=True),
            patch.dict(registry.CHARGER_BACKENDS, {"fake_charger": _FakeBackend}, clear=True),
        ):
            meter = registry.create_meter_backend("fake_meter", service)
            switch = registry.create_switch_backend("fake_switch", service)
            charger = registry.create_charger_backend("fake_charger", service)

        self.assertEqual(meter.config_path, "")
        self.assertEqual(switch.config_path, "")
        self.assertEqual(charger.config_path, "")


class TestBackendFactoryContracts(unittest.TestCase):
    def test_factory_boundary_guards_accept_only_valid_config_and_topology_sources(self) -> None:
        parser = _parser_from_text("[Topology]\nType=native_device\n[Charger]\nType=goe_charger\n")
        topology = parse_topology_config(parser)
        config_source = SimpleNamespace(config=parser)
        topology_source = SimpleNamespace(_topology_config=topology)

        self.assertIs(is_backend_config_source(config_source), True)
        self.assertIs(config_from_backend_service(config_source), parser)
        self.assertIs(is_backend_topology_source(topology_source), True)
        self.assertIs(topology_from_backend_service(topology_source), topology)

        for invalid in (object(), SimpleNamespace(config=object())):
            with self.subTest(config_source=invalid):
                self.assertIs(is_backend_config_source(invalid), False)
                self.assertIsNone(config_from_backend_service(invalid))

        for invalid in (object(), SimpleNamespace(_topology_config=object())):
            with self.subTest(topology_source=invalid):
                self.assertIs(is_backend_topology_source(invalid), False)
                self.assertIsNone(topology_from_backend_service(invalid))

    def test_adapter_type_from_config_path_prefers_adapter_section_over_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(
                temp_dir,
                "backend.ini",
                "[DEFAULT]\nType=wrong_default\n[Adapter]\nType= Template_Meter \n",
            )

            self.assertEqual(factory._adapter_type_from_config_path(path), "template_meter")

    def test_adapter_type_from_config_path_reads_default_when_adapter_section_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "backend.ini", "[DEFAULT]\nType= TASMOTA_METER \n")

            self.assertEqual(factory._adapter_type_from_config_path(path), "tasmota_meter")

    def test_adapter_type_from_config_path_returns_empty_when_type_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter_path = _write_config(temp_dir, "adapter.ini", "[Adapter]\nHost=192.168.1.20\n")
            default_path = _write_config(temp_dir, "default.ini", "[DEFAULT]\nHost=192.168.1.20\n")

            self.assertEqual(factory._adapter_type_from_config_path(adapter_path), "")
            self.assertEqual(factory._adapter_type_from_config_path(default_path), "")

    def test_adapter_type_from_config_path_validates_missing_path_and_missing_file(self) -> None:
        with self.assertRaises(ValueError) as no_path:
            factory._adapter_type_from_config_path(None)
        self.assertEqual(no_path.exception.args, ("Adapter-backed topology role requires ConfigPath",))

        with tempfile.TemporaryDirectory() as temp_dir:
            missing = str(Path(temp_dir) / "missing.ini")
            with self.assertRaises(FileNotFoundError) as missing_file:
                factory._adapter_type_from_config_path(missing)
        self.assertEqual(missing_file.exception.args, (missing,))

    def test_section_option_text_preserves_configparser_case_insensitive_options(self) -> None:
        parser = _parser_from_text("[Adapter]\nTyPe= Template_Meter \n")

        self.assertEqual(factory._section_option_text(parser["Adapter"], "type"), "template_meter")
        self.assertEqual(factory._section_option_text(parser["Adapter"], "host"), "")

    def test_topology_from_service_prefers_runtime_topology_and_handles_absent_config(self) -> None:
        parser = _parser_from_text("[Topology]\nType=native_device\n[Charger]\nType=goe_charger\n")
        runtime_topology = parse_topology_config(parser)
        conflicting = _parser_from_text("[Topology]\nType=simple_relay\n")

        self.assertIs(
            factory._topology_from_service(SimpleNamespace(_topology_config=runtime_topology, config=conflicting)),
            runtime_topology,
        )
        self.assertIsNone(factory._topology_from_service(SimpleNamespace()))
        self.assertIsNone(factory._topology_from_service(SimpleNamespace(config=object())))
        self.assertIsNone(
            factory._topology_from_service(SimpleNamespace(config=_parser_from_text("[DEFAULT]\nHost=x\n")))
        )

    def test_topology_from_service_parses_config_topology_when_runtime_topology_is_absent(self) -> None:
        parser = _parser_from_text("[Topology]\nType=native_device\n[Charger]\nType=goe_charger\n")

        topology = factory._topology_from_service(SimpleNamespace(config=parser))

        self.assertIsNotNone(topology)
        self.assertEqual(topology.topology.type, "native_device")
        self.assertEqual(topology.charger.type, "goe_charger")

    def test_runtime_from_topology_roles_maps_all_roles_and_flags_exactly(self) -> None:
        roles = factory._TopologyBackendRoles(
            meter_type="template_meter",
            meter_config_path=Path("/etc/meter.ini"),
            switch_type="template_switch",
            switch_config_path=Path("/etc/switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("/etc/charger.ini"),
        )

        runtime = factory._runtime_from_topology_roles(roles)

        self.assertEqual(runtime.backend_mode, "split")
        self.assertEqual(runtime.meter_type, "template_meter")
        self.assertEqual(runtime.meter_config_path, Path("/etc/meter.ini"))
        self.assertEqual(runtime.switch_type, "template_switch")
        self.assertEqual(runtime.switch_config_path, Path("/etc/switch.ini"))
        self.assertEqual(runtime.charger_type, "goe_charger")
        self.assertEqual(runtime.charger_config_path, Path("/etc/charger.ini"))
        self.assertIs(runtime.topology_configured, True)
        self.assertIs(runtime.primary_rpc_configured, False)

    def test_runtime_from_topology_roles_requires_complete_type_path_pairs_for_configured_flag(self) -> None:
        incomplete_roles = (
            factory._TopologyBackendRoles("template_meter", None, None, None, None, None),
            factory._TopologyBackendRoles(None, Path("/etc/meter.ini"), None, None, None, None),
            factory._TopologyBackendRoles(None, None, "template_switch", None, None, None),
            factory._TopologyBackendRoles(None, None, None, Path("/etc/switch.ini"), None, None),
            factory._TopologyBackendRoles(None, None, None, None, "goe_charger", None),
            factory._TopologyBackendRoles(None, None, None, None, None, Path("/etc/charger.ini")),
        )

        for roles in incomplete_roles:
            with self.subTest(roles=roles):
                runtime = factory._runtime_from_topology_roles(roles)
                self.assertIs(runtime.topology_configured, False)

    def test_backend_from_role_is_noop_for_absent_role_and_invokes_creator_for_present_role(self) -> None:
        calls: list[tuple[str, object, str]] = []
        service = object()

        def creator(role_type: str, creator_service: object, config_path: str) -> dict[str, object]:
            calls.append((role_type, creator_service, config_path))
            return {"role_type": role_type, "service": creator_service, "config_path": config_path}

        self.assertIsNone(factory._backend_from_role(None, Path("/ignored.ini"), service, creator))
        backend = factory._backend_from_role("template_meter", Path("/etc/meter.ini"), service, creator)

        self.assertEqual(backend, {"role_type": "template_meter", "service": service, "config_path": "/etc/meter.ini"})
        self.assertEqual(calls, [("template_meter", service, "/etc/meter.ini")])

    def test_direct_role_resolvers_pass_service_and_config_path_to_role_specific_creators(self) -> None:
        service = object()

        def fake_meter(role_type: str, creator_service: object, config_path: str) -> tuple[str, object, str]:
            return role_type, creator_service, config_path

        def fake_switch(role_type: str, creator_service: object, config_path: str) -> tuple[str, object, str]:
            return role_type, creator_service, config_path

        def fake_charger(role_type: str, creator_service: object, config_path: str) -> tuple[str, object, str]:
            return role_type, creator_service, config_path

        with (
            patch("venus_evcharger.backend.factory.create_meter_backend", side_effect=fake_meter),
            patch("venus_evcharger.backend.factory.create_switch_backend", side_effect=fake_switch),
            patch("venus_evcharger.backend.factory.create_charger_backend", side_effect=fake_charger),
        ):
            self.assertEqual(
                factory._direct_meter_backend("template_meter", Path("/etc/meter.ini"), service),
                ("template_meter", service, "/etc/meter.ini"),
            )
            self.assertEqual(
                factory._direct_switch_backend("template_switch", Path("/etc/switch.ini"), service),
                ("template_switch", service, "/etc/switch.ini"),
            )
            self.assertEqual(
                factory._direct_charger_backend("goe_charger", Path("/etc/charger.ini"), service),
                ("goe_charger", service, "/etc/charger.ini"),
            )

    def test_topology_role_helpers_distinguish_supported_measurement_modes(self) -> None:
        self.assertIs(factory._uses_native_or_empty_measurement("charger_native"), True)
        self.assertIs(factory._uses_native_or_empty_measurement("none"), True)
        self.assertIs(factory._uses_native_or_empty_measurement("external_meter"), False)
        self.assertIs(factory._uses_native_or_empty_measurement("fixed_reference"), False)

    def test_hybrid_topology_requires_charger_and_actuator_roles(self) -> None:
        actuator = ActuatorConfig(type="template_switch", config_path="/etc/switch.ini")
        charger = ChargerConfig(type="goe_charger", config_path="/etc/charger.ini")

        self.assertIs(
            factory._is_hybrid_topology(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="hybrid_topology"),
                    actuator=actuator,
                    charger=charger,
                )
            ),
            True,
        )
        self.assertIs(
            factory._is_hybrid_topology(
                EvChargerTopologyConfig(topology=TopologyConfig(type="hybrid_topology"), actuator=actuator)
            ),
            False,
        )
        self.assertIs(
            factory._is_hybrid_topology(
                EvChargerTopologyConfig(topology=TopologyConfig(type="hybrid_topology"), charger=charger)
            ),
            False,
        )

    def test_topology_backend_roles_resolve_external_meter_paths_and_native_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = _write_config(temp_dir, "meter.ini", "[Adapter]\nType=template_meter\n")
            switch_path = str(Path(temp_dir) / "switch.ini")
            charger_path = str(Path(temp_dir) / "charger.ini")
            external_roles = factory._topology_backend_roles(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="hybrid_topology"),
                    actuator=ActuatorConfig(type="template_switch", config_path=switch_path),
                    measurement=MeasurementConfig(type="external_meter", config_path=meter_path),
                    charger=ChargerConfig(type="goe_charger", config_path=charger_path),
                )
            )
            native_roles = factory._topology_backend_roles(
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="hybrid_topology"),
                    actuator=ActuatorConfig(type="template_switch", config_path=switch_path),
                    measurement=MeasurementConfig(type="charger_native"),
                    charger=ChargerConfig(type="goe_charger", config_path=charger_path),
                )
            )

        self.assertEqual(
            external_roles,
            factory._TopologyBackendRoles(
                meter_type="template_meter",
                meter_config_path=Path(meter_path),
                switch_type="template_switch",
                switch_config_path=Path(switch_path),
                charger_type="goe_charger",
                charger_config_path=Path(charger_path),
            ),
        )
        self.assertEqual(
            native_roles,
            factory._TopologyBackendRoles(
                meter_type=None,
                meter_config_path=None,
                switch_type="template_switch",
                switch_config_path=Path(switch_path),
                charger_type="goe_charger",
                charger_config_path=Path(charger_path),
            ),
        )

    def test_resolved_role_helpers_pass_runtime_paths_and_service_to_creators(self) -> None:
        service = object()
        runtime = BackendRuntimeSummary(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=Path("/etc/meter.ini"),
            switch_type="template_switch",
            switch_config_path=Path("/etc/switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("/etc/charger.ini"),
            topology_configured=True,
            primary_rpc_configured=False,
        )

        def fake_backend(role_type: str, creator_service: object, config_path: str) -> tuple[str, object, str]:
            return role_type, creator_service, config_path

        with (
            patch("venus_evcharger.backend.factory.create_meter_backend", side_effect=fake_backend),
            patch("venus_evcharger.backend.factory.create_switch_backend", side_effect=fake_backend),
            patch("venus_evcharger.backend.factory.create_charger_backend", side_effect=fake_backend),
        ):
            self.assertEqual(
                factory._resolved_meter_backend(runtime, service), ("template_meter", service, "/etc/meter.ini")
            )
            self.assertEqual(
                factory._resolved_switch_backend(runtime, service), ("template_switch", service, "/etc/switch.ini")
            )
            self.assertEqual(
                factory._resolved_charger_backend(runtime, service), ("goe_charger", service, "/etc/charger.ini")
            )

    def test_resolved_from_topology_builds_one_consistent_backend_bundle(self) -> None:
        service = object()
        roles = factory._TopologyBackendRoles(
            meter_type="template_meter",
            meter_config_path=Path("/etc/meter.ini"),
            switch_type="template_switch",
            switch_config_path=Path("/etc/switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("/etc/charger.ini"),
        )
        runtime = BackendRuntimeSummary(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=Path("/etc/meter.ini"),
            switch_type="template_switch",
            switch_config_path=Path("/etc/switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("/etc/charger.ini"),
            topology_configured=True,
            primary_rpc_configured=False,
        )

        with (
            patch(
                "venus_evcharger.backend.factory._topology_from_service", return_value="topology"
            ) as topology_from_service,
            patch("venus_evcharger.backend.factory._topology_backend_roles", return_value=roles) as topology_roles,
            patch(
                "venus_evcharger.backend.factory._runtime_from_topology_roles", return_value=runtime
            ) as runtime_from_roles,
            patch("venus_evcharger.backend.factory._direct_meter_backend", return_value="meter") as meter,
            patch("venus_evcharger.backend.factory._direct_switch_backend", return_value="switch") as switch,
            patch("venus_evcharger.backend.factory._direct_charger_backend", return_value="charger") as charger,
        ):
            resolved = factory._resolved_from_topology(service)

        self.assertEqual(
            resolved, factory.ResolvedBackends(runtime=runtime, meter="meter", switch="switch", charger="charger")
        )
        topology_from_service.assert_called_once_with(service)
        topology_roles.assert_called_once_with("topology")
        runtime_from_roles.assert_called_once_with(roles)
        meter.assert_called_once_with("template_meter", Path("/etc/meter.ini"), service)
        switch.assert_called_once_with("template_switch", Path("/etc/switch.ini"), service)
        charger.assert_called_once_with("goe_charger", Path("/etc/charger.ini"), service)

    def test_build_service_backends_prefers_topology_resolution_over_legacy_runtime(self) -> None:
        service = object()
        resolved = factory.ResolvedBackends(
            runtime=BackendRuntimeSummary("split", None, None, None, None, None, None, True, False),
            meter="meter",
            switch="switch",
            charger="charger",
        )

        with (
            patch(
                "venus_evcharger.backend.factory._resolved_from_topology", return_value=resolved
            ) as topology_resolver,
            patch("venus_evcharger.backend.factory.runtime_summary_from_service") as legacy_runtime,
        ):
            self.assertIs(factory.build_service_backends(service), resolved)

        topology_resolver.assert_called_once_with(service)
        legacy_runtime.assert_not_called()

    def test_build_service_backends_legacy_path_passes_one_runtime_and_service_to_each_resolver(self) -> None:
        service = object()
        runtime = BackendRuntimeSummary("split", None, None, None, None, None, None, False, False)

        with (
            patch("venus_evcharger.backend.factory._resolved_from_topology", return_value=None) as topology_resolver,
            patch(
                "venus_evcharger.backend.factory.runtime_summary_from_service", return_value=runtime
            ) as runtime_from_service,
            patch("venus_evcharger.backend.factory._resolved_meter_backend", return_value="meter") as meter,
            patch("venus_evcharger.backend.factory._resolved_switch_backend", return_value="switch") as switch,
            patch("venus_evcharger.backend.factory._resolved_charger_backend", return_value="charger") as charger,
        ):
            resolved = factory.build_service_backends(service)

        self.assertEqual(
            resolved, factory.ResolvedBackends(runtime=runtime, meter="meter", switch="switch", charger="charger")
        )
        topology_resolver.assert_called_once_with(service)
        runtime_from_service.assert_called_once_with(service)
        meter.assert_called_once_with(runtime, service)
        switch.assert_called_once_with(runtime, service)
        charger.assert_called_once_with(runtime, service)


class TestBackendProbeContracts(unittest.TestCase):
    def test_config_loader_rejects_an_invalid_boundary_result(self) -> None:
        with patch("venus_evcharger.backend.probe.load_required_backend_config", return_value=object()):
            with self.assertRaisesRegex(TypeError, "must return ConfigParser"):
                probe._config("config.ini")

    def test_probe_service_defaults_are_stable_for_standalone_backend_construction(self) -> None:
        session = object()
        with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
            service = probe._probe_service()

        self.assertIs(service.session, session)
        self.assertEqual(service.host, "")
        self.assertEqual(service.username, "")
        self.assertEqual(service.password, "")
        self.assertIs(service.use_digest_auth, False)
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.pm_component, "Switch")
        self.assertEqual(service.pm_id, 0)
        self.assertEqual(service.phase, "L1")
        self.assertEqual(service.max_current, 16.0)
        self.assertIsNone(service._last_voltage)

    def test_probe_service_from_wallbox_config_normalizes_defaults(self) -> None:
        config = _parser_from_text(
            "[DEFAULT]\n"
            "Host= 192.168.1.20 \nUsername=user\nPassword=pass\nDigestAuth=YES\n"
            "ShellyRequestTimeoutSeconds=3.5\nShellyComponent=EM\nShellyId=2\n"
            "Phase=L3\nMaxCurrent=10.5\n"
        )

        session = object()
        with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
            service = probe._probe_service_from_wallbox_config(config)

        self.assertIs(service.config, config)
        self.assertIs(service.session, session)
        self.assertEqual(service.host, "192.168.1.20")
        self.assertEqual(service.username, "user")
        self.assertEqual(service.password, "pass")
        self.assertTrue(service.use_digest_auth)
        self.assertEqual(service.shelly_request_timeout_seconds, 3.5)
        self.assertEqual(service.pm_component, "EM")
        self.assertEqual(service.pm_id, 2)
        self.assertEqual(service.phase, "L3")
        self.assertEqual(service.max_current, 10.5)
        self.assertIsNone(service._last_voltage)

    def test_probe_service_from_wallbox_config_preserves_defaults_and_bool_aliases(self) -> None:
        for raw in ("1", "true", "yes", "on"):
            with self.subTest(raw=raw):
                config = _parser_from_text(f"[DEFAULT]\nDigestAuth={raw}\n")
                service = probe._probe_service_from_wallbox_config(config)
                self.assertIs(service.use_digest_auth, True)
                self.assertEqual(service.host, "")
                self.assertEqual(service.username, "")
                self.assertEqual(service.password, "")
                self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
                self.assertEqual(service.pm_component, "Switch")
                self.assertEqual(service.pm_id, 0)
                self.assertEqual(service.phase, "L1")
                self.assertEqual(service.max_current, 16.0)

    def test_gateway_diagnostics_summary_uses_only_semantic_snapshot_fields(self) -> None:
        reader = _GatewayDiagnosticsReader()
        payload = probe._gateway_diagnostics_probe_summary(reader, now=110.0, max_age_seconds=20.0)
        self.assertEqual(
            payload,
            {
                "available": True,
                "fresh": True,
                "sequence": 7,
                "age_seconds": 10.0,
                "gateway_state": "ok",
                "gateway_stale": False,
                "discovery": reader.snapshot.discovery.to_payload(),
                "critical_unavailable_fields": [],
            },
        )
        self.assertFalse(probe._gateway_diagnostics_probe_summary(reader, now=120.1, max_age_seconds=20.0)["fresh"])

    def test_gateway_diagnostics_summary_reports_transport_unavailability(self) -> None:
        self.assertEqual(
            probe._gateway_diagnostics_probe_summary(_GatewayDiagnosticsReader(unavailable=True)),
            {"available": False, "fresh": False, "error": "offline"},
        )

    def test_adapter_type_contract_handles_adapter_default_and_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter_path = _write_config(temp_dir, "adapter.ini", "[Adapter]\nType= Template_Meter \n")
            adapter_missing_path = _write_config(temp_dir, "adapter-missing.ini", "[Adapter]\nHost=192.168.1.20\n")
            default_path = _write_config(temp_dir, "default.ini", "[DEFAULT]\nType= Shelly_Switch \n")
            legacy_path = _write_config(temp_dir, "legacy.ini", "[DEFAULT]\nHost=192.168.1.20\n")

            self.assertEqual(probe._adapter_type(adapter_path), "template_meter")
            self.assertEqual(probe._adapter_type(adapter_missing_path), "shelly_combined")
            self.assertEqual(probe._adapter_type(default_path), "shelly_switch")
            self.assertEqual(probe._adapter_type(legacy_path), "shelly_combined")

    def test_config_loader_delegates_to_required_backend_config_with_probe_label(self) -> None:
        parser = configparser.ConfigParser()
        with patch("venus_evcharger.backend.probe.load_required_backend_config", return_value=parser) as load_config:
            self.assertIs(probe._config("/tmp/backend.ini"), parser)
        load_config.assert_called_once_with("/tmp/backend.ini", "backend probe")

    def test_section_option_helpers_normalize_text_number_and_boolean_values(self) -> None:
        defaults = _parser_from_text("[DEFAULT]\nMixedCaseText= value \nFloatValue=0\nIntValue=0\nBoolValue=off\n")[
            "DEFAULT"
        ]

        self.assertEqual(probe._section_option_text(defaults, "mixedcasetext", "fallback"), "value")
        self.assertEqual(probe._section_option_text(defaults, "missing", "fallback"), "fallback")
        self.assertEqual(probe._section_option_float(defaults, "floatvalue", 2.0), 0.0)
        self.assertEqual(probe._section_option_int(defaults, "intvalue", 7), 0)
        self.assertEqual(probe._section_option_float(defaults, "missingfloat", 2.0), 2.0)
        self.assertEqual(probe._section_option_int(defaults, "missingint", 7), 7)
        self.assertFalse(probe._section_option_bool(defaults, "boolvalue", True))
        self.assertFalse(probe._section_option_bool(defaults, "missingbool"))
        self.assertTrue(probe._section_option_bool(defaults, "missingbool", True))

    def test_validate_backend_config_invokes_each_matching_role_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "multi.ini", "[Adapter]\nType=fake_multi\n")
            with (
                patch.dict(probe.METER_BACKENDS, {"fake_multi": _FakeMeterBackend}, clear=True),
                patch.dict(probe.SWITCH_BACKENDS, {"fake_multi": _FakeSwitchBackend}, clear=True),
                patch.dict(probe.CHARGER_BACKENDS, {"fake_multi": _FakeChargerBackend}, clear=True),
            ):
                payload = probe.validate_backend_config(path)

        self.assertEqual(payload, {"path": path, "type": "fake_multi", "roles": ["meter", "switch", "charger"]})

    def test_probe_meter_backend_payload_contract_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "meter.ini", "[Adapter]\nType=fake_meter\n")
            with patch.dict(probe.METER_BACKENDS, {"fake_meter": _FakeMeterBackend}, clear=True):
                payload = probe.probe_meter_backend(path)

        self.assertEqual(set(payload), {"path", "type", "shelly_profile", "component", "device_id", "meter"})
        self.assertEqual(payload["path"], path)
        self.assertEqual(payload["type"], "fake_meter")
        self.assertEqual(payload["shelly_profile"], "fake-meter-profile")
        self.assertEqual(payload["component"], "EM")
        self.assertEqual(payload["device_id"], 9)
        self.assertEqual(payload["meter"], {"power_w": 1234.0, "nested": {"path": "/tmp/meter"}})

    def test_probe_meter_backend_defaults_missing_settings_fields_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "meter.ini", "[Adapter]\nType=no_settings_meter\n")
            with patch.dict(probe.METER_BACKENDS, {"no_settings_meter": _NoSettingsMeterBackend}, clear=True):
                payload = probe.probe_meter_backend(path)

        self.assertIsNone(payload["shelly_profile"])
        self.assertIsNone(payload["component"])
        self.assertIsNone(payload["device_id"])

    def test_probe_switch_backend_payload_contract_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "switch.ini", "[Adapter]\nType=fake_switch\n")
            with patch.dict(probe.SWITCH_BACKENDS, {"fake_switch": _FakeSwitchBackend}, clear=True):
                payload = probe.probe_switch_backend(path)

        self.assertEqual(
            set(payload),
            {
                "path",
                "type",
                "shelly_profile",
                "component",
                "device_id",
                "capabilities",
                "phase_switch_targets",
                "phase_members",
                "feedback_readback",
                "interlock_readback",
                "switch_state",
            },
        )
        self.assertEqual(payload["path"], path)
        self.assertEqual(payload["type"], "fake_switch")
        self.assertEqual(payload["shelly_profile"], "fake-switch-profile")
        self.assertEqual(
            payload["capabilities"], {"switching_mode": "contactor", "supported_phase_selections": ["P1", "P1_P2_P3"]}
        )
        self.assertEqual(payload["phase_switch_targets"], {"P1": ["relay-1"]})
        self.assertEqual(payload["switch_state"], {"enabled": True, "phase_selection": "P1"})

    def test_probe_switch_backend_defaults_missing_optional_setting_maps_to_empty_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "switch.ini", "[Adapter]\nType=minimal_switch\n")
            with patch.dict(probe.SWITCH_BACKENDS, {"minimal_switch": _MinimalSwitchBackend}, clear=True):
                payload = probe.probe_switch_backend(path)

        self.assertEqual(payload["phase_switch_targets"], {})
        self.assertEqual(payload["phase_members"], {})
        self.assertIsNone(payload["feedback_readback"])
        self.assertIsNone(payload["interlock_readback"])

    def test_probe_switch_backend_defaults_missing_settings_fields_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "switch.ini", "[Adapter]\nType=no_settings_switch\n")
            with patch.dict(probe.SWITCH_BACKENDS, {"no_settings_switch": _NoSettingsSwitchBackend}, clear=True):
                payload = probe.probe_switch_backend(path)

        self.assertIsNone(payload["shelly_profile"])
        self.assertIsNone(payload["component"])
        self.assertIsNone(payload["device_id"])

    def test_probe_charger_backend_payload_contract_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "charger.ini", "[Adapter]\nType=fake_charger\n")
            with patch.dict(probe.CHARGER_BACKENDS, {"fake_charger": _FakeChargerBackend}, clear=True):
                payload = probe.probe_charger_backend(path)

        self.assertEqual(
            set(payload),
            {
                "path",
                "type",
                "profile_name",
                "transport_kind",
                "transport_unit_id",
                "transport_device",
                "transport_timeout_seconds",
                "transport_serial_port_owner",
                "transport_serial_retry_count",
                "transport_serial_retry_delay_seconds",
                "supported_phase_selections",
                "state_url",
                "state_actual_current_path",
                "state_power_watts_path",
                "state_energy_kwh_path",
                "state_status_path",
                "state_fault_path",
                "enable_url",
                "current_url",
                "phase_url",
            },
        )
        self.assertEqual(payload["profile_name"], "fake-charger-profile")
        self.assertEqual(payload["transport_kind"], "tcp")
        self.assertEqual(payload["transport_unit_id"], 8)
        self.assertEqual(payload["transport_device"], "/dev/ttyS1")
        self.assertEqual(payload["transport_timeout_seconds"], 3.5)
        self.assertEqual(payload["supported_phase_selections"], ["P1", "P1_P2"])
        self.assertEqual(payload["phase_url"], "/phase")

    def test_probe_charger_backend_defaults_missing_optional_setting_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "charger.ini", "[Adapter]\nType=minimal_charger\n")
            with patch.dict(probe.CHARGER_BACKENDS, {"minimal_charger": _MinimalChargerBackend}, clear=True):
                payload = probe.probe_charger_backend(path)

        self.assertEqual(payload["supported_phase_selections"], ["P1"])
        self.assertIsNone(payload["transport_kind"])
        self.assertIsNone(payload["state_url"])

    def test_probe_charger_backend_defaults_missing_settings_fields_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "charger.ini", "[Adapter]\nType=no_settings_charger\n")
            with patch.dict(probe.CHARGER_BACKENDS, {"no_settings_charger": _NoSettingsChargerBackend}, clear=True):
                payload = probe.probe_charger_backend(path)

        self.assertIsNone(payload["profile_name"])
        self.assertIsNone(payload["transport_kind"])
        self.assertEqual(payload["supported_phase_selections"], ["P1"])

    def test_read_charger_backend_payload_extends_probe_payload_with_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "charger.ini", "[Adapter]\nType=fake_charger\n")
            with patch.dict(probe.CHARGER_BACKENDS, {"fake_charger": _FakeChargerBackend}, clear=True):
                payload = probe.read_charger_backend(path)

        self.assertEqual(payload["path"], path)
        self.assertEqual(payload["type"], "fake_charger")
        self.assertEqual(payload["charger_state"], {"enabled": True, "current_amps": 12.0, "phase_selection": "P1_P2"})

    def test_main_help_and_probe_command_payload_contracts(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as help_exit:
            probe.main(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        self.assertIn("\nValidate or probe wallbox backend configs\n\npositional arguments:", stdout.getvalue())
        self.assertIn("read-charger", stdout.getvalue())

        with patch("venus_evcharger.backend.probe.validate_backend_config", return_value={"answer": 1}):
            self.assertEqual(probe._probe_command_payload("validate", "/tmp/config.ini"), {"answer": 1})
        with patch("venus_evcharger.backend.probe.validate_backend_config", return_value=[]):
            with self.assertRaisesRegex(
                TypeError,
                "Probe command 'validate' must return dict, got list",
            ):
                probe._probe_command_payload("validate", "/tmp/config.ini")

        stdout = io.StringIO()
        with (
            patch("venus_evcharger.backend.probe._probe_command_payload", return_value={"b": 2, "a": 1}),
            redirect_stdout(stdout),
        ):
            self.assertEqual(probe.main(["validate", "/tmp/config.ini"]), 0)
        self.assertEqual(stdout.getvalue(), '{\n  "a": 1,\n  "b": 2\n}\n')

    def test_validate_wallbox_config_payload_contract_is_complete(self) -> None:
        runtime = BackendRuntimeSummary("split", None, None, None, None, None, None, False, False)
        resolved = factory.ResolvedBackends(runtime=runtime, meter=None, switch="switch", charger="charger")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_config(temp_dir, "wallbox.ini", "[DEFAULT]\nHost=192.168.1.20\n")
            with (
                patch("venus_evcharger.backend.probe.build_service_backends", return_value=resolved),
                patch(
                    "venus_evcharger.backend.probe.backend_selection_view",
                    return_value={"mode": "split"},
                ),
                patch(
                    "venus_evcharger.backend.probe._gateway_diagnostics_probe_summary",
                    return_value={"available": True},
                ),
            ):
                payload = probe.validate_wallbox_config(path)

        self.assertEqual(set(payload), {"path", "runtime", "selection", "resolved_roles", "gateway_diagnostics"})
        self.assertEqual(payload["path"], path)
        self.assertEqual(payload["selection"], {"mode": "split"})
        self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": True, "charger": True})
        self.assertEqual(payload["gateway_diagnostics"], {"available": True})


if __name__ == "__main__":
    unittest.main()
