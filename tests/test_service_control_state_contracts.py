# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral edge contracts for the composed service control boundary."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.control import CONTROL_API_COMMAND_SCOPE_REQUIREMENTS, ControlCommand, ControlResult
from venus_evcharger.core.contracts import (
    CONTROL_API_ENDPOINTS,
    CONTROL_API_EXPERIMENTAL_ENDPOINTS,
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_API_STABLE_ENDPOINTS,
    CONTROL_COMMAND_NAMES,
    CONTROL_COMMAND_SOURCES,
)
from venus_evcharger.service import control as control_module
from venus_evcharger.service import control_runtime as runtime_module
from venus_evcharger.service import control_state_config as config_module
from venus_evcharger.service import control_state_core as core_module
from venus_evcharger.service import control_state_meta as meta_module
from venus_evcharger.service.control import ServiceControlFacade
from venus_evcharger.service.control_runtime import ControlRuntime
from venus_evcharger.service.control_state_config import ControlStateConfig
from venus_evcharger.service.control_state_core import ControlStateCore
from venus_evcharger.service.control_state_meta import ControlStateMeta
from venus_evcharger.service.control_state_operational import ControlStateOperational
from venus_evcharger.service.control_state_victron import ControlStateVictron


def _command() -> ControlCommand:
    return ControlCommand(
        name="set_mode",
        target="mode",
        value=2,
        source="http",
        detail="scheduled",
        command_id="cmd-1",
        idempotency_key="idem-1",
    )


def _result(command: ControlCommand) -> ControlResult:
    return ControlResult(
        command=command,
        status="applied",
        accepted=True,
        applied=True,
        persisted=False,
        reversible_failure=False,
        external_side_effect_started=False,
        detail="ok",
    )


class _HealthRuntime:
    def __init__(self, stale: bool = False) -> None:
        self.stale = stale
        self.observed_at: list[float] = []

    def update_is_stale(self, now: float) -> bool:
        self.observed_at.append(now)
        return self.stale


class TestServiceControlFacadeContracts(unittest.TestCase):
    def test_facade_wires_owned_components_and_live_runtime_callbacks(self) -> None:
        service = SimpleNamespace(auto=MagicMock())
        core = object()
        operational = object()
        config = object()
        victron = object()
        audit = SimpleNamespace(count=MagicMock(return_value=7))
        idempotency = SimpleNamespace(count=MagicMock(return_value=11))
        runtime = SimpleNamespace(
            audit_trail=MagicMock(return_value=audit),
            idempotency_store=MagicMock(return_value=idempotency),
            running=True,
        )
        meta = object()

        with (
            patch.object(control_module, "ControlStateCore", return_value=core) as core_factory,
            patch.object(control_module, "ControlStateOperational", return_value=operational) as operational_factory,
            patch.object(control_module, "ControlStateConfig", return_value=config) as config_factory,
            patch.object(control_module, "ControlStateVictron", return_value=victron) as victron_factory,
            patch.object(control_module, "ControlRuntime", return_value=runtime) as runtime_factory,
            patch.object(control_module, "ControlStateMeta", return_value=meta) as meta_factory,
        ):
            facade = ServiceControlFacade(service)

        self.assertIs(facade.service, service)
        self.assertIs(facade.core, core)
        self.assertIs(facade.operational, operational)
        self.assertIs(facade.config, config)
        self.assertIs(facade.victron, victron)
        self.assertIs(facade.runtime, runtime)
        self.assertIs(facade.meta, meta)
        core_factory.assert_called_once_with(service)
        operational_factory.assert_called_once_with(service)
        config_factory.assert_called_once_with(service)
        victron_factory.assert_called_once_with(service)
        runtime_factory.assert_called_once_with(service, facade)
        meta_factory.assert_called_once()
        args, kwargs = meta_factory.call_args
        self.assertEqual(args, (service, core, operational, config, victron))
        self.assertEqual(set(kwargs), {"audit_count", "idempotency_count", "control_running"})
        self.assertEqual(kwargs["audit_count"](), 7)
        self.assertEqual(kwargs["idempotency_count"](), 11)
        self.assertIs(kwargs["control_running"](), True)
        audit.count.assert_called_once_with()
        idempotency.count.assert_called_once_with()

    def test_facade_public_defaults_are_forwarded_verbatim(self) -> None:
        command = _command()
        result = _result(command)
        facade = ServiceControlFacade(SimpleNamespace(auto=MagicMock()))
        facade.runtime = MagicMock()
        facade.core = MagicMock()

        facade.publish_command_event(command, result)
        facade.runtime.publish_command_event.assert_called_once_with(command, result, replayed=False)

        facade.core.command_from_payload.return_value = command
        self.assertIs(facade.control_command_from_payload({"name": "set_mode"}), command)
        facade.core.command_from_payload.assert_called_once_with({"name": "set_mode"}, "http")

        facade.runtime.record_command_audit.return_value = {"audit": 1}
        self.assertEqual(
            facade.record_command_audit(
                command=command,
                result=result,
                error=None,
                replayed=False,
                scope="control_basic",
                client_host="127.0.0.1",
                status_code=200,
            ),
            {"audit": 1},
        )
        facade.runtime.record_command_audit.assert_called_once_with(
            command=command,
            result=result,
            error=None,
            replayed=False,
            scope="control_basic",
            client_host="127.0.0.1",
            status_code=200,
            transport="http",
        )

    def test_facade_delegates_transport_commands_and_state_payloads(self) -> None:
        command = _command()
        result = _result(command)
        auto = MagicMock()
        auto.handle_command.return_value = result
        facade = ServiceControlFacade(SimpleNamespace(auto=auto))
        facade.runtime = MagicMock()
        facade.core = MagicMock()
        facade.operational = MagicMock()
        facade.config = MagicMock()
        facade.meta = MagicMock()
        facade.victron = MagicMock()

        facade.meta.event_snapshot_payload.return_value = {"snapshot": True}
        facade.start_server()
        facade.stop_server()
        facade.publish_command_event(command, result, replayed=True)
        facade.runtime.start_server.assert_called_once_with({"snapshot": True})
        facade.runtime.stop_server.assert_called_once_with()
        facade.runtime.publish_command_event.assert_called_once_with(command, result, replayed=True)

        facade.core.command_from_payload.return_value = command
        self.assertIs(facade.control_command_from_payload({"name": "set_mode"}, "dbus"), command)
        facade.core.command_from_payload.assert_called_once_with({"name": "set_mode"}, "dbus")
        self.assertIs(facade.handle_control_command(command), result)
        auto.handle_command.assert_called_once_with(command)

        facade.runtime.record_command_audit.return_value = {"audit": 1}
        self.assertEqual(
            facade.record_command_audit(
                command=command,
                result=result,
                error={"code": "none"},
                replayed=False,
                scope="control_basic",
                client_host="127.0.0.1",
                status_code=200,
                transport="unix",
            ),
            {"audit": 1},
        )
        facade.runtime.record_command_audit.assert_called_once_with(
            command=command,
            result=result,
            error={"code": "none"},
            replayed=False,
            scope="control_basic",
            client_host="127.0.0.1",
            status_code=200,
            transport="unix",
        )

        delegated_calls = (
            (facade.idempotency_store, facade.runtime.idempotency_store, "idempotency"),
            (facade.rate_limiter, facade.runtime.rate_limiter, "rate-limiter"),
            (facade.event_bus, facade.runtime.event_bus, "event-bus"),
            (facade.state_token, facade.meta.state_token, "state-token"),
            (facade.capabilities_payload, facade.meta.capabilities_payload, "capabilities"),
            (facade.automation_payload, facade.meta.automation_payload, "automation"),
            (facade.build_payload, facade.meta.build_payload, "build"),
            (facade.config_effective_payload, facade.config.effective_payload, "config"),
            (facade.contracts_payload, facade.meta.contracts_payload, "contracts"),
            (facade.dbus_diagnostics_payload, facade.core.dbus_diagnostics_payload, "diagnostics"),
            (facade.event_snapshot_payload, facade.meta.event_snapshot_payload, "events"),
            (facade.health_payload, facade.meta.health_payload, "health"),
            (facade.healthz_payload, facade.meta.healthz_payload, "healthz"),
            (facade.operational_payload, facade.operational.payload, "operational"),
            (facade.runtime_payload, facade.core.runtime_payload, "runtime"),
            (facade.summary_payload, facade.core.summary_payload, "summary"),
            (facade.topology_payload, facade.core.topology_payload, "topology"),
            (facade.update_payload, facade.core.update_payload, "update"),
            (facade.version_payload, facade.meta.version_payload, "version"),
            (facade.victron_bias_recommendation_payload, facade.victron.recommendation_payload, "victron"),
        )
        for facade_call, component_call, expected in delegated_calls:
            with self.subTest(expected=expected):
                component_call.return_value = expected
                self.assertEqual(facade_call(), expected)


class TestControlRuntimeMutationContracts(unittest.TestCase):
    def test_runtime_owns_exact_initial_resources(self) -> None:
        service = SimpleNamespace()
        http_service = MagicMock()
        events = object()
        with patch.object(runtime_module, "ControlApiEventBus", return_value=events) as event_factory:
            runtime = ControlRuntime(service, http_service)

        self.assertIs(runtime.service, service)
        self.assertIs(runtime.http_service, http_service)
        self.assertIsNone(runtime._audit)
        self.assertIsNone(runtime._idempotency)
        self.assertIsNone(runtime._rate_limiter)
        self.assertIs(runtime._events, events)
        self.assertIsNone(runtime._server)
        self.assertFalse(runtime.running)
        event_factory.assert_called_once_with()

    def test_runtime_component_factories_preserve_configured_and_default_values(self) -> None:
        configured_service = SimpleNamespace(
            control_api_audit_max_entries="17",
            control_api_audit_path=" /run/audit.json ",
            control_api_idempotency_max_entries="19",
            control_api_idempotency_path=" /run/idempotency.json ",
            control_api_rate_limit_max_requests="23",
            control_api_rate_limit_window_seconds="7.5",
            control_api_critical_cooldown_seconds="2.5",
        )
        configured = ControlRuntime(configured_service, MagicMock())
        audit = object()
        idempotency = object()
        rate_limiter = object()
        with (
            patch.object(runtime_module, "ControlApiAuditTrail", return_value=audit) as audit_factory,
            patch.object(
                runtime_module,
                "ControlApiIdempotencyStore",
                return_value=idempotency,
            ) as idempotency_factory,
            patch.object(runtime_module, "ControlApiRateLimiter", return_value=rate_limiter) as limiter_factory,
        ):
            self.assertIs(configured.audit_trail(), audit)
            self.assertIs(configured.audit_trail(), audit)
            self.assertIs(configured.idempotency_store(), idempotency)
            self.assertIs(configured.idempotency_store(), idempotency)
            self.assertIs(configured.rate_limiter(), rate_limiter)
            self.assertIs(configured.rate_limiter(), rate_limiter)

        audit_factory.assert_called_once_with(history_limit=17, path="/run/audit.json")
        idempotency_factory.assert_called_once_with(history_limit=19, path="/run/idempotency.json")
        limiter_factory.assert_called_once_with(
            max_requests=23,
            window_seconds=7.5,
            critical_cooldown_seconds=2.5,
        )

        defaults = ControlRuntime(SimpleNamespace(), MagicMock())
        with (
            patch.object(runtime_module, "ControlApiAuditTrail", return_value=object()) as audit_factory,
            patch.object(runtime_module, "ControlApiIdempotencyStore", return_value=object()) as idempotency_factory,
            patch.object(runtime_module, "ControlApiRateLimiter", return_value=object()) as limiter_factory,
        ):
            defaults.audit_trail()
            defaults.idempotency_store()
            defaults.rate_limiter()

        audit_factory.assert_called_once_with(history_limit=200, path="")
        idempotency_factory.assert_called_once_with(history_limit=200, path="")
        limiter_factory.assert_called_once_with(
            max_requests=30,
            window_seconds=5.0,
            critical_cooldown_seconds=2.0,
        )

    def test_runtime_audit_and_event_defaults_preserve_complete_semantics(self) -> None:
        runtime = ControlRuntime(SimpleNamespace(), MagicMock())
        command = ControlCommand(
            name="set_mode",
            target="mode",
            value=2,
            source="",
            detail="scheduled",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = _result(command)
        audit = MagicMock()
        audit.append.side_effect = lambda payload: payload
        runtime._audit = audit
        with patch.object(runtime_module.time, "time", return_value=123.5):
            entry = runtime.record_command_audit(
                command=command,
                result=result,
                error=None,
                replayed=False,
                scope="control_basic",
                client_host="127.0.0.1",
                status_code=202,
            )
        self.assertEqual(
            entry,
            {
                "timestamp": 123.5,
                "transport": "http",
                "scope": "control_basic",
                "client_host": "127.0.0.1",
                "status_code": 202,
                "replayed": False,
                "command": {
                    "name": "set_mode",
                    "target": "mode",
                    "value": 2,
                    "source": "http",
                    "detail": "scheduled",
                    "command_id": "cmd-1",
                    "idempotency_key": "idem-1",
                },
                "result": {
                    "status": "applied",
                    "accepted": True,
                    "applied": True,
                    "persisted": False,
                    "reversible_failure": False,
                    "external_side_effect_started": False,
                    "detail": "ok",
                },
                "error": {},
            },
        )
        audit.append.assert_called_once_with(entry)

        runtime._events = MagicMock()
        runtime.publish_command_event(command, result)
        runtime._events.publish.assert_called_once_with(
            "command",
            {
                "command": {
                    "name": "set_mode",
                    "target": "mode",
                    "value": 2,
                    "source": "internal",
                    "detail": "scheduled",
                    "command_id": "cmd-1",
                    "idempotency_key": "idem-1",
                },
                "result": {
                    "status": "applied",
                    "accepted": True,
                    "applied": True,
                    "persisted": False,
                    "reversible_failure": False,
                    "external_side_effect_started": False,
                    "detail": "ok",
                },
                "replayed": False,
            },
        )

    def test_runtime_server_defaults_and_disabled_boundary_are_exact(self) -> None:
        disabled = ControlRuntime(SimpleNamespace(), MagicMock())
        with patch.object(runtime_module, "LocalControlApiHttpServer") as server_factory:
            disabled.start_server({"mode": 0})
        server_factory.assert_not_called()
        self.assertFalse(disabled.running)

        service = SimpleNamespace(
            control_api_enabled=True,
            control_api_host="0.0.0.0",
            control_api_localhost_only=False,
        )
        http_service = MagicMock()
        runtime = ControlRuntime(service, http_service)
        server = MagicMock(
            bound_host="127.0.0.1",
            bound_port=8765,
            bound_unix_socket_path="/run/control.sock",
        )
        runtime.publish_state_event = MagicMock()
        with patch.object(runtime_module, "LocalControlApiHttpServer", return_value=server) as server_factory:
            runtime.start_server({"mode": 2})

        server_factory.assert_called_once_with(
            http_service,
            host="0.0.0.0",
            port=0,
            auth_token="",
            read_token="",
            control_token="",
            admin_token="",
            update_token="",
            localhost_only=False,
            unix_socket_path="",
        )
        server.start.assert_called_once_with()
        self.assertEqual(service.control_api_listen_host, "127.0.0.1")
        self.assertEqual(service.control_api_listen_port, 8765)
        self.assertEqual(service.control_api_bound_unix_socket_path, "/run/control.sock")
        runtime.publish_state_event.assert_called_once_with({"mode": 2})

        default_service = SimpleNamespace(control_api_enabled=True)
        default_http_service = MagicMock()
        default_runtime = ControlRuntime(default_service, default_http_service)
        default_server = MagicMock(
            bound_host="127.0.0.1",
            bound_port=0,
            bound_unix_socket_path="",
        )
        with patch.object(
            runtime_module,
            "LocalControlApiHttpServer",
            return_value=default_server,
        ) as default_factory:
            default_runtime.start_server({"mode": 0})
        default_factory.assert_called_once_with(
            default_http_service,
            host="127.0.0.1",
            port=0,
            auth_token="",
            read_token="",
            control_token="",
            admin_token="",
            update_token="",
            localhost_only=True,
            unix_socket_path="",
        )


class TestControlStateConfigContracts(unittest.TestCase):
    def test_converters_accept_supported_values_and_reject_opaque_objects(self) -> None:
        self.assertIs(config_module._identity("unchanged"), "unchanged")
        self.assertTrue(config_module._as_bool(1))
        self.assertEqual(config_module._as_int("12"), 12)
        self.assertEqual(config_module._as_int(13), 13)
        self.assertEqual(config_module._as_float(b"2.5"), 2.5)
        self.assertEqual(config_module._as_float(3), 3.0)
        self.assertEqual(config_module._as_str(4), "4")
        self.assertTrue(config_module._token_configured(" token "))
        self.assertFalse(config_module._token_configured("  "))
        with self.assertRaisesRegex(TypeError, "Expected an integer-compatible value, got object"):
            config_module._as_int(object())
        with self.assertRaisesRegex(TypeError, "Expected a float-compatible value, got object"):
            config_module._as_float(object())
        self.assertEqual(config_module._source_id(SimpleNamespace()), "")
        self.assertEqual(config_module._profile_name(SimpleNamespace()), "")

    def test_energy_source_normalization_rejects_non_collections(self) -> None:
        self.assertEqual(config_module._energy_sources(object()), ())
        self.assertEqual(config_module._energy_sources(SimpleNamespace(auto_energy_sources=None)), ())
        self.assertEqual(config_module._energy_sources(SimpleNamespace(auto_energy_sources="battery")), ())
        self.assertEqual(config_module._energy_sources(SimpleNamespace(auto_energy_sources=7)), ())
        sources = (SimpleNamespace(source_id="bat", profile_name="dbus-battery"),)
        self.assertEqual(config_module._energy_sources(SimpleNamespace(auto_energy_sources=sources)), sources)

    def test_effective_payload_composes_all_sections_and_filters_blank_source_ids(self) -> None:
        owner = SimpleNamespace(
            deviceinstance="61",
            product_name="Venus EV Charger",
            custom_name="Garage",
            service_name="must-not-leak",
            host="192.0.2.10",
            control_api_port="8765",
            control_api_read_token="read-token",
            control_api_control_token="",
            companion_publication_enabled=True,
            companion_dbus_bridge_enabled=True,
            companion_grid_authoritative_source=17,
            companion_grid_hold_seconds="4.5",
            auto_energy_sources=(
                SimpleNamespace(source_id="battery-main", profile_name="dbus-battery"),
                SimpleNamespace(source_id=" ", profile_name="ignored"),
            ),
            auto_use_combined_battery_soc=False,
            auto_battery_discharge_balance_warn_error_watts="321.5",
            auto_battery_discharge_balance_victron_bias_kp="0.42",
            auto_battery_discharge_balance_victron_bias_service="must-not-leak",
            auto_battery_discharge_balance_victron_bias_path="/must-not-leak",
            companion_battery_service_name="must-not-leak",
            companion_source_battery_service_prefix="must-not-leak",
        )

        def backend_type(_owner: object, role: str, _default: str) -> str:
            return f"{role}-backend"

        backend_mode = MagicMock(return_value="split")
        backend_type_mock = MagicMock(side_effect=backend_type)
        with (
            patch.object(config_module, "backend_mode_for_service", backend_mode),
            patch.object(config_module, "backend_type_for_service", backend_type_mock),
            patch.object(
                config_module,
                "energy_source_profile_details",
                side_effect=lambda profile: {"profile_name": str(profile)},
            ),
        ):
            payload = ControlStateConfig(owner).effective_payload()

        state = payload["state"]
        self.assertEqual((payload["ok"], payload["api_version"], payload["kind"]), (True, "v1", "config-effective"))
        self.assertEqual(state["instance_id"], 61)
        self.assertEqual(state["product_name"], "Venus EV Charger")
        self.assertEqual(state["display_name"], "Garage")
        self.assertEqual(state["host"], "192.0.2.10")
        self.assertEqual(state["control_api_port"], 8765)
        self.assertTrue(state["control_api_read_token_configured"])
        self.assertFalse(state["control_api_control_token_configured"])
        self.assertTrue(state["companion_publication_enabled"])
        self.assertEqual(state["companion_grid_authoritative_source"], "17")
        self.assertEqual(state["companion_grid_hold_seconds"], 4.5)
        self.assertEqual(state["backend_mode"], "split")
        self.assertEqual(state["meter_backend"], "meter-backend")
        self.assertEqual(state["switch_backend"], "switch-backend")
        self.assertEqual(state["charger_backend"], "charger-backend")
        backend_mode.assert_called_once_with(owner, "combined")
        self.assertEqual(
            backend_type_mock.call_args_list,
            [
                call(owner, "meter", "na"),
                call(owner, "switch", "na"),
                call(owner, "charger", "na"),
            ],
        )
        self.assertEqual(state["auto_energy_source_ids"], ["battery-main", " "])
        self.assertEqual(state["auto_energy_source_profiles"], {"battery-main": "dbus-battery"})
        self.assertEqual(
            state["auto_energy_source_profile_details"],
            {"battery-main": {"profile_name": "dbus-battery"}},
        )
        self.assertEqual(state["auto_energy_source_count"], 2)
        self.assertFalse(state["auto_use_combined_battery_soc"])
        self.assertEqual(state["auto_battery_discharge_balance_warn_error_watts"], 321.5)
        self.assertEqual(state["auto_battery_discharge_balance_victron_bias_kp"], 0.42)
        forbidden_identity_fields = {
            "deviceinstance",
            "service_name",
            "companion_dbus_bridge_enabled",
            "companion_battery_service_name",
            "companion_source_battery_service_prefix",
            "auto_battery_discharge_balance_victron_bias_service",
            "auto_battery_discharge_balance_victron_bias_path",
        }
        self.assertTrue(forbidden_identity_fields.isdisjoint(state))

    def test_energy_source_defaults_keep_combined_soc_enabled(self) -> None:
        self.assertEqual(
            config_module._config_effective_energy_sources(SimpleNamespace()),
            {
                "auto_use_combined_battery_soc": True,
                "auto_energy_source_ids": [],
                "auto_energy_source_profiles": {},
                "auto_energy_source_profile_details": {},
                "auto_energy_source_count": 0,
            },
        )


class TestControlStateCoreContracts(unittest.TestCase):
    def _service(self, command: object) -> SimpleNamespace:
        diagnostics = SimpleNamespace(counters={"writes": 2}, ages={"/Mode": 1.25})
        publisher = MagicMock()
        publisher.diagnostic_snapshot.return_value = diagnostics
        return SimpleNamespace(
            auto=SimpleNamespace(command_from_payload=MagicMock(return_value=command)),
            state=SimpleNamespace(
                summary=MagicMock(return_value="mode=2"),
                current=MagicMock(return_value={"health": "ok"}),
            ),
            controllers=SimpleNamespace(runtime=SimpleNamespace(publisher=publisher)),
            time_now=lambda: 123.0,
            supported_phase_selections=(),
            active_phase_selection="P1_P2_P3",
            requested_phase_selection="P1_P2",
            deviceinstance=73,
            product_name="Venus EV Charger",
            custom_name="Garage Wallbox",
            service_name="must-not-leak",
            connection_name="test",
            _software_update_current_version="1.0.0",
            _software_update_available_version="1.1.0",
            _software_update_available=True,
            _software_update_state="available",
            _software_update_detail="release ready",
            _software_update_last_check_at=10.0,
            _software_update_last_run_at=11.0,
            _software_update_last_result="ok",
            _software_update_run_requested_at=12.0,
            _software_update_next_check_at=13.0,
            _software_update_boot_auto_due_at=14.0,
            _software_update_no_update_active=True,
        )

    def test_core_command_default_and_error_contract_are_exact(self) -> None:
        command = _command()
        service = self._service(command)
        core = ControlStateCore(service)
        payload = {"name": "set_mode"}

        self.assertIs(core.command_from_payload(payload), command)
        service.auto.command_from_payload.assert_called_once_with(payload, source="http")

        service.auto.command_from_payload.return_value = {"name": "set_mode"}
        with self.assertRaisesRegex(TypeError, "^write controller returned non-ControlCommand payload$"):
            core.command_from_payload(payload)

    def test_core_summary_runtime_and_diagnostics_builder_contracts_are_exact(self) -> None:
        service = self._service(_command())
        core = ControlStateCore(service)
        passthrough = lambda payload: payload
        with (
            patch.object(
                core_module,
                "normalized_state_api_summary_fields",
                side_effect=passthrough,
            ) as normalize_summary,
            patch.object(
                core_module,
                "normalized_state_api_runtime_fields",
                side_effect=passthrough,
            ) as normalize_runtime,
            patch.object(
                core_module,
                "normalized_state_api_dbus_diagnostics_fields",
                side_effect=passthrough,
            ) as normalize_diagnostics,
        ):
            summary = core.summary_payload()
            runtime = core.runtime_payload()
            diagnostics = core.dbus_diagnostics_payload()

        expected_summary = {
            "ok": True,
            "api_version": "v1",
            "kind": "summary",
            "summary": "mode=2",
        }
        expected_runtime = {
            "ok": True,
            "api_version": "v1",
            "kind": "runtime",
            "state": {"health": "ok"},
        }
        expected_diagnostics = {
            "ok": True,
            "api_version": "v1",
            "kind": "dbus-diagnostics",
            "state": {"writes": 2, "/Mode": 1.25},
        }
        self.assertEqual(summary, expected_summary)
        self.assertEqual(runtime, expected_runtime)
        self.assertEqual(diagnostics, expected_diagnostics)
        normalize_summary.assert_called_once_with(expected_summary)
        normalize_runtime.assert_called_once_with(expected_runtime)
        normalize_diagnostics.assert_called_once_with(expected_diagnostics)
        service.controllers.runtime.publisher.diagnostic_snapshot.assert_called_once_with(123.0)

    def test_core_topology_builder_preserves_configured_values_and_backend_calls(self) -> None:
        service = self._service(_command())
        core = ControlStateCore(service)
        backend_mode = MagicMock(return_value="split")
        backend_type = MagicMock(side_effect=lambda _service, role, _default: f"{role}-backend")
        passthrough = lambda payload: payload
        with (
            patch.object(core_module, "backend_mode_for_service", backend_mode),
            patch.object(core_module, "backend_type_for_service", backend_type),
            patch.object(
                core_module,
                "normalized_state_api_topology_fields",
                side_effect=passthrough,
            ) as normalize,
        ):
            payload = core.topology_payload()

        expected = {
            "ok": True,
            "api_version": "v1",
            "kind": "topology",
            "state": {
                "backend_mode": "split",
                "meter_backend": "meter-backend",
                "switch_backend": "switch-backend",
                "charger_backend": "charger-backend",
                "active_phase_selection": "P1_P2_P3",
                "requested_phase_selection": "P1_P2",
                "supported_phase_selections": ["P1"],
                "available_modes": [0, 1, 2],
                "instance_id": 73,
                "product_name": "Venus EV Charger",
                "display_name": "Garage Wallbox",
                "connection_name": "test",
            },
        }
        self.assertEqual(payload, expected)
        normalize.assert_called_once_with(expected)
        backend_mode.assert_called_once_with(service, "combined")
        self.assertEqual(
            backend_type.call_args_list,
            [
                call(service, "meter", "na"),
                call(service, "switch", "na"),
                call(service, "charger", "na"),
            ],
        )

    def test_core_topology_builder_preserves_all_defaults(self) -> None:
        service = SimpleNamespace()
        core = ControlStateCore(service)
        passthrough = lambda payload: payload
        with (
            patch.object(core_module, "backend_mode_for_service", return_value="combined"),
            patch.object(core_module, "backend_type_for_service", return_value="na"),
            patch.object(
                core_module,
                "normalized_state_api_topology_fields",
                side_effect=passthrough,
            ),
        ):
            self.assertEqual(
                core.topology_payload(),
                {
                    "ok": True,
                    "api_version": "v1",
                    "kind": "topology",
                    "state": {
                        "backend_mode": "combined",
                        "meter_backend": "na",
                        "switch_backend": "na",
                        "charger_backend": "na",
                        "active_phase_selection": "P1",
                        "requested_phase_selection": "P1",
                        "supported_phase_selections": ["P1"],
                        "available_modes": [0, 1, 2],
                        "instance_id": 0,
                        "product_name": "",
                        "display_name": "",
                        "connection_name": "",
                    },
                },
            )

    def test_core_update_builder_preserves_configured_values_and_defaults(self) -> None:
        passthrough = lambda payload: payload
        service = self._service(_command())
        core = ControlStateCore(service)
        with patch.object(
            core_module,
            "normalized_state_api_update_fields",
            side_effect=passthrough,
        ) as normalize:
            payload = core.update_payload()
        expected = {
            "ok": True,
            "api_version": "v1",
            "kind": "update",
            "state": {
                "current_version": "1.0.0",
                "available_version": "1.1.0",
                "available": True,
                "state": "available",
                "detail": "release ready",
                "last_check_at": 10.0,
                "last_run_at": 11.0,
                "last_result": "ok",
                "run_requested_at": 12.0,
                "next_check_at": 13.0,
                "boot_auto_due_at": 14.0,
                "no_update_active": True,
            },
        }
        self.assertEqual(payload, expected)
        normalize.assert_called_once_with(expected)

        with patch.object(
            core_module,
            "normalized_state_api_update_fields",
            side_effect=passthrough,
        ):
            self.assertEqual(
                ControlStateCore(SimpleNamespace()).update_payload(),
                {
                    "ok": True,
                    "api_version": "v1",
                    "kind": "update",
                    "state": {
                        "current_version": "",
                        "available_version": "",
                        "available": False,
                        "state": "idle",
                        "detail": "",
                        "last_check_at": None,
                        "last_run_at": None,
                        "last_result": "",
                        "run_requested_at": None,
                        "next_check_at": None,
                        "boot_auto_due_at": None,
                        "no_update_active": False,
                    },
                },
            )

    def test_core_diagnostics_use_wall_clock_when_service_clock_is_absent(self) -> None:
        service = self._service(_command())
        del service.time_now
        core = ControlStateCore(service)
        with patch.object(core_module.time, "time", return_value=55.5):
            core.dbus_diagnostics_payload()
        service.controllers.runtime.publisher.diagnostic_snapshot.assert_called_once_with(55.5)

    def test_core_builders_normalize_command_state_diagnostics_topology_and_update(self) -> None:
        command = _command()
        service = self._service(command)
        core = ControlStateCore(service)
        self.assertIs(core.command_from_payload({"name": "set_mode"}, "dbus"), command)
        self.assertEqual(core.summary_payload()["summary"], "mode=2")
        self.assertEqual(core.runtime_payload()["state"], {"health": "ok"})
        diagnostics = core.dbus_diagnostics_payload()
        self.assertEqual(diagnostics["state"], {"writes": 2, "/Mode": 1.25})
        service.controllers.runtime.publisher.diagnostic_snapshot.assert_called_once_with(123.0)

        with (
            patch.object(core_module, "backend_mode_for_service", return_value="split"),
            patch.object(core_module, "backend_type_for_service", side_effect=lambda _service, role, _default: role),
        ):
            topology = core.topology_payload()["state"]
        self.assertEqual(topology["supported_phase_selections"], ["P1"])
        self.assertEqual(topology["requested_phase_selection"], "P1_P2")
        update = core.update_payload()["state"]
        self.assertEqual(update["current_version"], "1.0.0")
        self.assertTrue(update["available"])

    def test_core_rejects_invalid_commands_and_handles_non_mapping_diagnostics_and_time(self) -> None:
        service = self._service({"name": "set_mode"})
        core = ControlStateCore(service)
        with self.assertRaisesRegex(TypeError, "non-ControlCommand"):
            core.command_from_payload({"name": "set_mode"})

        service.controllers.runtime.publisher.diagnostic_snapshot.return_value = SimpleNamespace(counters=[], ages=None)
        service.time_now = lambda: "not-a-number"
        with patch.object(core_module.time, "time", return_value=77.0):
            self.assertEqual(core.dbus_diagnostics_payload()["state"], {})
        service.time_now = 5
        service.supported_phase_selections = ("P1", "P1_P2_P3")
        with (
            patch.object(core_module.time, "time", return_value=88.0),
            patch.object(core_module, "backend_mode_for_service", return_value="combined"),
            patch.object(core_module, "backend_type_for_service", return_value="backend"),
        ):
            self.assertEqual(core.topology_payload()["state"]["supported_phase_selections"], ["P1", "P1_P2_P3"])


class TestControlStateMetaContracts(unittest.TestCase):
    def _meta(self, service: SimpleNamespace) -> ControlStateMeta:
        core = MagicMock(spec=ControlStateCore)
        core.summary_payload.return_value = {"state": {"mode": 2}}
        core.update_payload.return_value = {"state": {"state": "idle"}}
        core.topology_payload.return_value = {"state": {"backend_mode": "split"}}
        core.dbus_diagnostics_payload.return_value = {
            "state": {"/Status": 2, "/Auto/Health": "ok", "/Unrelated": "hidden"}
        }
        operational = MagicMock(spec=ControlStateOperational)
        operational.payload.return_value = {"state": {"auto_decision": {"reason": "ready"}}}
        config = MagicMock(spec=ControlStateConfig)
        victron = MagicMock(spec=ControlStateVictron)
        return ControlStateMeta(
            service,
            core,
            operational,
            config,
            victron,
            audit_count=lambda: 3,
            idempotency_count=lambda: 4,
            control_running=lambda: True,
        )

    def test_meta_owns_exact_providers_and_callbacks(self) -> None:
        service = object()
        core = MagicMock(spec=ControlStateCore)
        operational = MagicMock(spec=ControlStateOperational)
        config = MagicMock(spec=ControlStateConfig)
        victron = MagicMock(spec=ControlStateVictron)
        audit_count = MagicMock(return_value=3)
        idempotency_count = MagicMock(return_value=4)
        control_running = MagicMock(return_value=True)

        meta = ControlStateMeta(
            service,
            core,
            operational,
            config,
            victron,
            audit_count=audit_count,
            idempotency_count=idempotency_count,
            control_running=control_running,
        )

        self.assertIs(meta.service, service)
        self.assertIs(meta.core, core)
        self.assertIs(meta.operational, operational)
        self.assertIs(meta.config, config)
        self.assertIs(meta.victron, victron)
        self.assertIs(meta._audit_count, audit_count)
        self.assertIs(meta._idempotency_count, idempotency_count)
        self.assertIs(meta._control_running, control_running)

    def test_healthz_version_build_and_contract_schemas_are_exact(self) -> None:
        service = SimpleNamespace(
            control_api_enabled=True,
            _software_update_current_version="2.1.0",
            firmware_version="2.0.0",
            product_name="EVCS",
            deviceinstance=60,
            service_name="must-not-leak",
            hardware_version="gx",
            connection_name="network",
            runtime_state_path="/run/state.json",
        )
        meta = self._meta(service)
        self.assertEqual(
            meta.healthz_payload(),
            {
                "ok": True,
                "api_version": "v1",
                "kind": "healthz",
                "state": {
                    "alive": True,
                    "control_api_enabled": True,
                    "control_api_running": True,
                },
            },
        )
        self.assertEqual(
            meta.version_payload(),
            {
                "ok": True,
                "api_version": "v1",
                "kind": "version",
                "state": {
                    "service_version": "2.1.0",
                    "api_version": "v1",
                    "product_name": "EVCS",
                    "instance_id": 60,
                },
            },
        )
        self.assertEqual(
            meta.build_payload(),
            {
                "ok": True,
                "api_version": "v1",
                "kind": "build",
                "state": {
                    "product_name": "EVCS",
                    "hardware_version": "gx",
                    "firmware_version": "2.0.0",
                    "connection_name": "network",
                    "runtime_state_path": "/run/state.json",
                },
            },
        )
        self.assertEqual(
            meta.contracts_payload(),
            {
                "ok": True,
                "api_version": "v1",
                "kind": "contracts",
                "state": {
                    "active_api_version": "v1",
                    "openapi_endpoint": "/v1/openapi.json",
                    "capabilities_endpoint": "/v1/capabilities",
                    "versioning_document": "API_VERSIONING.md",
                    "control_document": "CONTROL_API.md",
                    "state_document": "STATE_API.md",
                    "stable_endpoints": sorted(CONTROL_API_STABLE_ENDPOINTS),
                    "experimental_endpoints": sorted(CONTROL_API_EXPERIMENTAL_ENDPOINTS),
                },
            },
        )

    def test_automation_schema_is_exact(self) -> None:
        service = SimpleNamespace(runtime=_HealthRuntime())
        meta = self._meta(service)
        meta.health_payload = MagicMock(return_value={"state": {"health_reason": "ok"}})
        with patch.object(meta, "state_token", return_value="token-1"):
            payload = meta.automation_payload()
        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "automation",
                "state": {
                    "state_token": "token-1",
                    "command_endpoint": "/v1/control/command",
                    "events_endpoint": "/v1/events",
                    "state_endpoints": sorted(CONTROL_API_STATE_ENDPOINTS),
                    "safe_write": {
                        "if_match_header": "If-Match",
                        "state_token_header": "X-State-Token",
                        "idempotency_key_header": "Idempotency-Key",
                        "command_id_header": "X-Command-Id",
                        "recommended_flow": (
                            "read /v1/state/automation, then POST command with If-Match and Idempotency-Key"
                        ),
                    },
                    "writable": {
                        "command_names": sorted(CONTROL_COMMAND_NAMES),
                        "scope_requirements": dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
                    },
                    "operational": {"auto_decision": {"reason": "ready"}},
                    "auto_decision": {"reason": "ready"},
                    "health": {"health_reason": "ok"},
                    "topology": {"backend_mode": "split"},
                    "diagnostics": {"/Status": 2, "/Auto/Health": "ok"},
                },
            },
        )

    def test_health_schema_and_fault_boundary_are_exact(self) -> None:
        runtime = _HealthRuntime(stale=True)
        service = SimpleNamespace(
            runtime=runtime,
            control_api_enabled=True,
            control_api_localhost_only=False,
            control_api_listen_host="0.0.0.0",
            control_api_listen_port=8765,
            control_api_bound_unix_socket_path="/run/control.sock",
            control_api_audit_path="/run/audit.json",
            control_api_idempotency_path="/run/idempotency.json",
            _last_health_reason="shelly-offline",
            _last_health_code=7,
            _runtime_overrides_active=True,
            _last_successful_update_at=12.0,
            _last_recovery_attempt_at=11.0,
        )
        meta = self._meta(service)
        with (
            patch.object(meta_module.time, "time", return_value=100.0),
            patch.object(meta_module, "evse_fault_reason", return_value="fault-input") as fault_reason,
            patch.object(
                meta_module,
                "normalized_fault_state",
                return_value=("fault-normalized", True),
            ) as normalize_fault,
        ):
            payload = meta.health_payload()
        self.assertEqual(runtime.observed_at, [100.0])
        fault_reason.assert_called_once_with("shelly-offline")
        normalize_fault.assert_called_once_with("fault-input")
        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "kind": "health",
                "state": {
                    "health_reason": "shelly-offline",
                    "health_code": 7,
                    "fault_active": True,
                    "fault_reason": "fault-normalized",
                    "runtime_overrides_active": True,
                    "control_api_enabled": True,
                    "control_api_running": True,
                    "control_api_transport": "http",
                    "listen_host": "0.0.0.0",
                    "listen_port": 8765,
                    "unix_socket_path": "/run/control.sock",
                    "control_api_localhost_only": False,
                    "command_audit_entries": 3,
                    "command_audit_path": "/run/audit.json",
                    "idempotency_entries": 4,
                    "idempotency_path": "/run/idempotency.json",
                    "update_stale": True,
                    "last_successful_update_at": 12.0,
                    "last_recovery_attempt_at": 11.0,
                },
            },
        )

    def test_health_defaults_and_localhost_presence_are_distinct(self) -> None:
        configured_localhost = SimpleNamespace(
            runtime=_HealthRuntime(),
            control_api_localhost_only=True,
        )
        configured_payload = self._meta(configured_localhost).health_payload()["state"]
        self.assertEqual(configured_payload["health_reason"], "init")
        self.assertTrue(configured_payload["control_api_localhost_only"])

        missing_localhost = SimpleNamespace(runtime=_HealthRuntime())
        missing_payload = self._meta(missing_localhost).health_payload()["state"]
        self.assertEqual(missing_payload["health_reason"], "init")
        self.assertTrue(missing_payload["control_api_localhost_only"])

    def test_capabilities_schema_and_backend_boundary_are_exact(self) -> None:
        service = SimpleNamespace(
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            control_api_read_token="read-token",
            control_api_control_token="control-token",
            control_api_auth_token="legacy-token",
            control_api_localhost_only=False,
            control_api_bound_unix_socket_path="/run/control.sock",
        )
        meta = self._meta(service)
        backend_mode = MagicMock(return_value="split")
        backend_type = MagicMock(side_effect=lambda _service, role, _default: f"{role}-backend")
        with (
            patch.object(meta_module, "backend_mode_for_service", backend_mode),
            patch.object(meta_module, "backend_type_for_service", backend_type),
        ):
            payload = meta.capabilities_payload()

        features = {
            "command_audit_trail": True,
            "dbus_diagnostics_state": True,
            "event_stream": True,
            "event_kind_filters": True,
            "event_retry_hints": True,
            "http_control_command": True,
            "idempotency_tracking": True,
            "optimistic_concurrency": True,
            "per_command_request_schemas": True,
            "rate_limiting": True,
            "runtime_only_idempotency_persistence": True,
            "multi_phase_selection": True,
            "phase_selection_write": True,
            "read_api": True,
            "runtime_override_write": True,
            "software_update_trigger": True,
            "state_reads": True,
        }
        self.assertEqual(
            payload,
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "auth_required": True,
                "read_auth_required": True,
                "control_auth_required": True,
                "localhost_only": False,
                "unix_socket_path": "/run/control.sock",
                "auth_header": "Authorization: Bearer <token>",
                "auth_scopes": ["control_admin", "control_basic", "read", "update_admin"],
                "command_names": sorted(CONTROL_COMMAND_NAMES),
                "command_scope_requirements": dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
                "command_sources": sorted(CONTROL_COMMAND_SOURCES),
                "state_endpoints": sorted(CONTROL_API_STATE_ENDPOINTS),
                "endpoints": sorted(CONTROL_API_ENDPOINTS),
                "available_modes": [0, 1, 2],
                "supported_phase_selections": ["P1", "P1_P2", "P1_P2_P3"],
                "features": features,
                "topology": {
                    "backend_mode": "split",
                    "meter_backend": "meter-backend",
                    "switch_backend": "switch-backend",
                    "charger_backend": "charger-backend",
                },
                "versioning": {
                    "stable_endpoints": sorted(CONTROL_API_STABLE_ENDPOINTS),
                    "experimental_endpoints": sorted(CONTROL_API_EXPERIMENTAL_ENDPOINTS),
                    "breaking_change_policy": (
                        "Stable v1 endpoints require a version bump for breaking changes; "
                        "experimental endpoints may evolve within v1."
                    ),
                },
            },
        )
        backend_mode.assert_called_once_with(service, "combined")
        self.assertEqual(
            backend_type.call_args_list,
            [
                call(service, "meter", "na"),
                call(service, "switch", "na"),
                call(service, "charger", "na"),
            ],
        )

    def test_capability_auth_and_phase_truth_tables_are_exact(self) -> None:
        scenarios = (
            (
                SimpleNamespace(
                    supported_phase_selections=("P1",),
                    control_api_read_token="read-token",
                    control_api_control_token="",
                    control_api_auth_token="",
                    control_api_localhost_only=True,
                ),
                (True, True, False, False, True),
            ),
            (
                SimpleNamespace(
                    supported_phase_selections=("P1", "P1_P2"),
                    control_api_read_token="",
                    control_api_control_token="control-token",
                    control_api_auth_token="",
                ),
                (True, True, True, True, True),
            ),
            (
                SimpleNamespace(
                    control_api_read_token="",
                    control_api_control_token="",
                    control_api_auth_token="legacy-token",
                ),
                (True, True, True, False, True),
            ),
            (SimpleNamespace(), (False, False, False, False, True)),
        )
        with (
            patch.object(meta_module, "backend_mode_for_service", return_value="combined"),
            patch.object(meta_module, "backend_type_for_service", return_value="na"),
        ):
            for service, expected in scenarios:
                with self.subTest(service=service):
                    payload = self._meta(service).capabilities_payload()
                    observed = (
                        payload["auth_required"],
                        payload["read_auth_required"],
                        payload["control_auth_required"],
                        payload["features"]["multi_phase_selection"],
                        payload["localhost_only"],
                    )
                    self.assertEqual(observed, expected)

    def test_state_token_uses_canonical_json_and_sha256(self) -> None:
        meta = self._meta(SimpleNamespace(runtime=_HealthRuntime()))
        payload = {"summary": {"state": {"mode": 2}}}
        meta.state_token_payload = MagicMock(return_value=payload)
        digest = MagicMock()
        digest.hexdigest.return_value = "digest"
        with (
            patch.object(meta_module.json, "dumps", return_value="encoded-json") as dumps,
            patch.object(meta_module.hashlib, "sha256", return_value=digest) as sha256,
        ):
            self.assertEqual(meta.state_token(), "digest")
        dumps.assert_called_once_with(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        sha256.assert_called_once_with(b"encoded-json")
        digest.hexdigest.assert_called_once_with()

    def test_meta_payloads_expose_health_build_version_contracts_and_deterministic_token(self) -> None:
        runtime = _HealthRuntime(stale=True)
        service = SimpleNamespace(
            runtime=runtime,
            control_api_enabled=True,
            control_api_localhost_only=False,
            control_api_listen_host="127.0.0.1",
            control_api_listen_port=8765,
            control_api_bound_unix_socket_path="/run/control.sock",
            control_api_audit_path="/run/audit.json",
            control_api_idempotency_path="/run/idempotency.json",
            _last_health_reason="shelly-offline",
            _last_health_code=7,
            _runtime_overrides_active=True,
            _last_successful_update_at=12.0,
            _last_recovery_attempt_at=11.0,
            _software_update_current_version="",
            firmware_version="2.0.0",
            hardware_version="gx",
            product_name="EVCS",
            service_name="com.example.evcharger",
            connection_name="network",
            runtime_state_path="/run/state.json",
        )
        meta = self._meta(service)
        with patch.object(meta_module.time, "time", return_value=100.0):
            health = meta.health_payload()
        self.assertEqual(runtime.observed_at, [100.0])
        self.assertTrue(health["state"]["update_stale"])
        self.assertEqual(health["state"]["command_audit_entries"], 3)
        self.assertEqual(meta.healthz_payload()["state"]["control_api_running"], True)
        self.assertEqual(meta.version_payload()["state"]["service_version"], "2.0.0")
        service._software_update_current_version = "2.1.0"
        self.assertEqual(meta.version_payload()["state"]["service_version"], "2.1.0")
        self.assertEqual(meta.build_payload()["state"]["hardware_version"], "gx")
        self.assertIn("/v1/capabilities", meta.contracts_payload()["state"]["stable_endpoints"])
        snapshot = meta.event_snapshot_payload()
        self.assertEqual(set(snapshot), {"summary", "operational", "health", "update", "topology"})
        self.assertEqual(meta.state_token(), meta.state_token())
        self.assertEqual(len(meta.state_token()), 64)

    def test_automation_and_capabilities_compose_only_public_semantics(self) -> None:
        service = SimpleNamespace(
            runtime=_HealthRuntime(),
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            control_api_read_token="read",
            control_api_control_token="control",
            control_api_auth_token="legacy",
            control_api_localhost_only=False,
            control_api_bound_unix_socket_path="/run/control.sock",
        )
        meta = self._meta(service)
        with patch.object(meta, "state_token", return_value="token-1"):
            automation = meta.automation_payload()["state"]
        self.assertEqual(automation["state_token"], "token-1")
        self.assertEqual(automation["auto_decision"], {"reason": "ready"})
        self.assertEqual(automation["diagnostics"], {"/Status": 2, "/Auto/Health": "ok"})

        with (
            patch.object(meta_module, "backend_mode_for_service", return_value="split"),
            patch.object(meta_module, "backend_type_for_service", side_effect=lambda _service, role, _default: role),
        ):
            capabilities = meta.capabilities_payload()
        self.assertTrue(capabilities["auth_required"])
        self.assertTrue(capabilities["features"]["multi_phase_selection"])
        self.assertEqual(capabilities["supported_phase_selections"], ["P1", "P1_P2", "P1_P2_P3"])

        minimal = self._meta(SimpleNamespace(runtime=_HealthRuntime()))
        with (
            patch.object(meta_module, "backend_mode_for_service", return_value="combined"),
            patch.object(meta_module, "backend_type_for_service", return_value="na"),
        ):
            defaults = minimal.capabilities_payload()
        self.assertFalse(defaults["auth_required"])
        self.assertTrue(defaults["localhost_only"])
        self.assertEqual(defaults["supported_phase_selections"], ["P1"])

    def test_meta_helpers_normalize_missing_and_malformed_values(self) -> None:
        service = SimpleNamespace(empty="", number="9", phases=[])
        self.assertEqual(meta_module._mapping_value([]), {})
        self.assertEqual(meta_module._mapping_value({"a": 1}), {"a": 1})
        self.assertEqual(meta_module._payload_state({"state": "invalid"}), {})
        self.assertEqual(meta_module._automation_diagnostics_subset({"/Status": 1, "/Other": 2}), {"/Status": 1})
        self.assertFalse(meta_module._optional_bool_attr(service, "missing"))
        self.assertEqual(meta_module._optional_int_attr(service, "missing"), 0)
        self.assertEqual(meta_module._optional_int_attr(service, "number"), 9)
        self.assertEqual(meta_module._optional_text_attr(service, "missing", "fallback"), "fallback")
        self.assertEqual(meta_module._optional_text_attr(service, "empty", "fallback"), "")
        self.assertEqual(meta_module._configured_phase_selections(service), ("P1",))
        service.supported_phase_selections = ("P1", "P1_P2")
        self.assertEqual(meta_module._configured_phase_selections(service), ("P1", "P1_P2"))


if __name__ == "__main__":
    unittest.main()
