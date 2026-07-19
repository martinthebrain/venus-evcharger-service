# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the explicit wallbox service composition components."""

from __future__ import annotations

import configparser
import unittest
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from requests.auth import HTTPDigestAuth

from venus_evcharger.backend.shelly_io_types import ShellyHttpSession, ShellyPmStatus
from venus_evcharger.control import ControlCommand, ControlResult
from venus_evcharger.control.models import ControlCommandSource
from venus_evcharger.service import controller_owner as owner_module
from venus_evcharger.service.auto_facade import ServiceAutoFacade
from venus_evcharger.service.composition_guards import (
    require_auto_input_service,
    require_backend_target,
    require_dbus_input_service,
    require_publish_service,
    require_update_cycle_service,
)
from venus_evcharger.service.controller_owner import (
    RuntimeControllers,
    ServiceControllerOwner,
    ServiceFunctionBundle,
)
from venus_evcharger.service.runtime_facade import ServiceRuntimeFacade
from venus_evcharger.service.state_facade import ServiceStateFacade
from venus_evcharger.service.update_facade import ServiceUpdateFacade


class _CallLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def add(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))


class _RuntimeDouble(_CallLog):
    direct_publish = True

    def initialize_runtime_support(self) -> None:
        self.add("initialize_runtime_support")

    def reset_system_bus(self) -> None:
        self.add("reset_system_bus")

    def init_worker_state(self) -> None:
        self.add("init_worker_state")

    def ensure_worker_state(self) -> None:
        self.add("ensure_worker_state")

    def mark_mainloop_thread(self) -> None:
        self.add("mark_mainloop_thread")

    def dbus_publish_direct_allowed(self) -> bool:
        self.add("dbus_publish_direct_allowed")
        return self.direct_publish

    def assert_dbus_mainloop_thread(self, operation: str = "dbus access") -> None:
        self.add("assert_dbus_mainloop_thread", operation)

    def enqueue_dbus_publish_values(self, values: list[tuple[str, object]], current: float) -> bool:
        self.add("enqueue_dbus_publish_values", values, current)
        return True

    def enqueue_dbus_publish_fields(self, fields: list[tuple[str, object]], current: float) -> bool:
        self.add("enqueue_dbus_publish_fields", fields, current)
        return True

    def enqueue_dbus_update_index_bump(self, current: float) -> None:
        self.add("enqueue_dbus_update_index_bump", current)

    def enqueue_companion_dbus_publish(self, now: float | None = None) -> bool:
        self.add("enqueue_companion_dbus_publish", now)
        return True

    def flush_dbus_publish_queue(self) -> bool:
        self.add("flush_dbus_publish_queue")
        return True

    def start_update_worker(self) -> None:
        self.add("start_update_worker")

    def schedule_update_cycle(self) -> bool:
        self.add("schedule_update_cycle")
        return True

    def start_control_command_worker(self) -> None:
        self.add("start_control_command_worker")

    def enqueue_control_command(self, command: ControlCommand) -> bool:
        self.add("enqueue_control_command", command)
        return True

    def mainloop_heartbeat_tick(self) -> bool:
        self.add("mainloop_heartbeat_tick")
        return True

    def start_mainloop_watchdog(self) -> None:
        self.add("start_mainloop_watchdog")

    def update_worker_snapshot(self, **fields: object) -> None:
        self.add("update_worker_snapshot", **fields)

    def get_worker_snapshot(self) -> dict[str, object]:
        self.add("get_worker_snapshot")
        return {"heartbeat_at": 12.0}

    def ensure_observability_state(self) -> None:
        self.add("ensure_observability_state")

    def is_update_stale(self, now: float | None = None) -> bool:
        self.add("is_update_stale", now)
        return False

    def watchdog_recover(self, now: float) -> None:
        self.add("watchdog_recover", now)

    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        self.add("warning_throttled", key, interval_seconds, message, *args, **kwargs)

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> None:
        self.add("write_auto_audit_event", reason, cached)

    def mark_failure(self, key: str) -> None:
        self.add("mark_failure", key)

    def mark_recovery(self, key: str, message: str, *args: object) -> None:
        self.add("mark_recovery", key, message, *args)

    def source_retry_ready(self, key: str, now: float | None = None) -> bool:
        self.add("source_retry_ready", key, now)
        return True

    def source_retry_remaining(self, key: str, now: float | None = None) -> int:
        self.add("source_retry_remaining", key, now)
        return 4

    def delay_source_retry(
        self,
        key: str,
        now: float | None = None,
        delay_seconds: float | None = None,
    ) -> None:
        self.add("delay_source_retry", key, now, delay_seconds)


class _AutoDouble(_CallLog):
    def clear_auto_samples(self) -> None:
        self.add("clear_auto_samples")

    def mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None:
        self.add("mark_relay_changed", relay_on, now)

    def set_health(self, reason: str, cached: bool = False) -> None:
        self.add("set_health", reason, cached)

    def auto_decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | int | None,
        grid_power: float | None,
    ) -> bool:
        self.add("auto_decide_relay", relay_on, pv_power, battery_soc, grid_power)
        return not relay_on


class _WriteDouble(_CallLog):
    def __init__(self) -> None:
        super().__init__()
        self.accepted = True

    @staticmethod
    def _command(path: str, value: object, source: ControlCommandSource) -> ControlCommand:
        del value
        return ControlCommand(name="set_mode", path=path, value=1, source=source)

    def build_control_command(
        self,
        path: str,
        value: object,
        *,
        source: ControlCommandSource = "dbus",
    ) -> ControlCommand:
        self.add("build_control_command", path, value, source)
        return self._command(path, value, source)

    def build_control_command_from_payload(
        self,
        payload: dict[str, object],
        *,
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        self.add("build_control_command_from_payload", payload, source)
        return self._command(str(payload["path"]), payload.get("value"), source)

    def handle_control_command(self, command: ControlCommand) -> ControlResult:
        self.add("handle_control_command", command)
        if self.accepted:
            return ControlResult.applied_result(command)
        return ControlResult.rejected_result(command)


class _DbusInputDouble(_CallLog):
    def invalidate_auto_pv_services(self) -> None:
        self.add("invalidate_auto_pv_services")

    def invalidate_auto_battery_service(self) -> None:
        self.add("invalidate_auto_battery_service")


class _AutoInputDouble(_CallLog):
    def stop_helper(self, force: bool = False) -> None:
        self.add("stop_helper", force)

    def spawn_helper(self, now: float | None = None) -> None:
        self.add("spawn_helper", now)

    def ensure_helper_process(self, now: float | None = None) -> None:
        self.add("ensure_helper_process", now)

    def refresh_snapshot(self, now: float | None = None) -> None:
        self.add("refresh_snapshot", now)


class _ResponseDouble:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return {}


class _SessionDouble:
    def get(
        self,
        *,
        url: str,
        timeout: float,
        auth: HTTPDigestAuth | tuple[str, str] | None = None,
    ) -> _ResponseDouble:
        del url, timeout, auth
        return _ResponseDouble()


class _ShellyDouble(_CallLog):
    def request(self, url: str) -> dict[str, object]:
        self.add("request", url)
        return {"url": url}

    def request_with_session(self, session: ShellyHttpSession, url: str) -> dict[str, object]:
        self.add("request_with_session", session, url)
        return {"url": url}

    def rpc_call(self, method: str, **params: str | int | float | bool) -> dict[str, object]:
        self.add("rpc_call", method, **params)
        return {"method": method}

    def rpc_call_with_session(
        self,
        session: ShellyHttpSession,
        method: str,
        **params: str | int | float | bool,
    ) -> dict[str, object]:
        self.add("rpc_call_with_session", session, method, **params)
        return {"method": method}

    def worker_fetch_pm_status(self) -> dict[str, object]:
        self.add("worker_fetch_pm_status")
        return {"output": True}

    def build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus:
        self.add("build_local_pm_status", relay_on)
        return {"output": relay_on}

    def publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus:
        self.add("publish_local_pm_status", relay_on, now)
        return {"output": relay_on}

    def queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
        self.add("queue_relay_command", relay_on, now)

    def peek_pending_relay_command(self) -> tuple[bool | None, float | None]:
        self.add("peek_pending_relay_command")
        return True, 9.0

    def clear_pending_relay_command(self, relay_on: bool) -> None:
        self.add("clear_pending_relay_command", relay_on)

    def worker_apply_pending_relay_command(self) -> None:
        self.add("worker_apply_pending_relay_command")

    def start_io_worker(self) -> None:
        self.add("start_io_worker")

    def fetch_pm_status(self) -> dict[str, object]:
        self.add("fetch_pm_status")
        return {"output": False}

    def set_relay(self, on: bool) -> dict[str, object]:
        self.add("set_relay", on)
        return {"output": on}

    def phase_selection_requires_pause(self) -> bool:
        self.add("phase_selection_requires_pause")
        return True

    def set_phase_selection(self, selection: object) -> str:
        self.add("set_phase_selection", selection)
        return str(selection)


class _PublisherDouble(_CallLog):
    def ensure_state(self) -> None:
        self.add("ensure_state")

    def publish_field(
        self,
        field: str,
        value: object,
        now: float | None = None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        self.add("publish_field", field, value, now, interval_seconds, force)
        return True

    def bump_update_index(self, now: float | None = None) -> None:
        self.add("bump_update_index", now)

    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: dict[str, dict[str, float]],
        now: float | None,
    ) -> bool:
        self.add("publish_live_measurements", power, voltage, total_current, phase_data, now)
        return True

    def publish_energy_time_measurements(
        self,
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
        now: float | None,
    ) -> bool:
        self.add(
            "publish_energy_time_measurements",
            energy_forward,
            phase_energies,
            charging_time,
            session_energy,
            now,
        )
        return True

    def publish_config_paths(self, startstop_display: int, now: float | None) -> bool:
        self.add("publish_config_paths", startstop_display, now)
        return True

    def publish_diagnostic_paths(self, now: float) -> bool:
        self.add("publish_diagnostic_paths", now)
        return True


class _StateDouble(_CallLog):
    def state_summary(self) -> str:
        self.add("state_summary")
        return "ready"

    def current_runtime_state(self) -> dict[str, object]:
        self.add("current_runtime_state")
        return {"mode": 2}

    def load_runtime_state(self) -> None:
        self.add("load_runtime_state")

    def save_runtime_state(self) -> None:
        self.add("save_runtime_state")

    def save_runtime_overrides(self) -> None:
        self.add("save_runtime_overrides")

    def flush_runtime_overrides(self, now: float | None = None) -> None:
        self.add("flush_runtime_overrides", now)

    def validate_runtime_config(self) -> None:
        self.add("validate_runtime_config")

    def load_config(self) -> configparser.ConfigParser:
        self.add("load_config")
        return configparser.ConfigParser()


class _CompanionDouble(_CallLog):
    def start(self) -> None:
        self.add("start")

    def stop(self) -> None:
        self.add("stop")

    def publish(self, now: float | None = None) -> bool:
        self.add("publish", now)
        return True


class _UpdateDouble(_CallLog):
    def update(self) -> bool:
        self.add("update")
        return True

    def sign_of_life(self) -> bool:
        self.add("sign_of_life")
        return True


class _BootstrapDouble(_CallLog):
    def initialize_service(self) -> None:
        self.add("initialize_service")


@dataclass
class _OwnerDouble:
    functions: ServiceFunctionBundle
    state: _StateDouble
    bootstrap: _BootstrapDouble
    runtime: RuntimeControllers


def _functions() -> ServiceFunctionBundle:
    def normalize_mode(value: object) -> int:
        return int(str(value))

    def mode_uses_auto_logic(value: object) -> bool:
        return normalize_mode(value) in (1, 2)

    return ServiceFunctionBundle(
        normalize_phase=lambda value: str(value),
        normalize_mode=normalize_mode,
        mode_uses_auto_logic=mode_uses_auto_logic,
        month_window=lambda config, month, start, end: (config, month, start, end),
        age_seconds=lambda then, now: int(float(now or 0) - float(then or 0)),
        health_code=lambda reason: len(reason),
        phase_values=lambda power, voltage, phase, mode: {
            phase: {"power": float(power), "voltage": float(voltage), "current": 1.0, "mode": float(len(mode))}
        },
        read_version=lambda path: path,
        gobject=object(),
        script_path="service.py",
        config_path="config.ini",
        auto_input_helper_path="helper.py",
        formatters={},
    )


def _owner() -> tuple[_OwnerDouble, _RuntimeDouble, _WriteDouble]:
    runtime = _RuntimeDouble()
    write = _WriteDouble()
    controllers = RuntimeControllers(
        runtime=runtime,
        auto=_AutoDouble(),
        publisher=_PublisherDouble(),
        shelly=_ShellyDouble(),
        write=write,
        auto_input=_AutoInputDouble(),
        dbus_input=_DbusInputDouble(),
        update=_UpdateDouble(),
        companion=_CompanionDouble(),
    )
    return _OwnerDouble(_functions(), _StateDouble(), _BootstrapDouble(), controllers), runtime, write


class ServiceCompositionContractTests(unittest.TestCase):
    def test_boundary_contracts_accept_complete_hosts_and_reject_scalars(self) -> None:
        def enqueue_fields(fields: list[tuple[str, object]], current: float) -> bool:
            return bool(fields) and current >= 0

        def update_snapshot(**fields: object) -> None:
            del fields

        def retry_ready(key: str, now: float) -> bool:
            return bool(key) and now >= 0

        def worker_snapshot() -> dict[str, object]:
            return {}

        def mode_uses_auto(mode: object) -> bool:
            return mode in (1, 2)

        def decide_relay(
            relay_on: bool,
            pv: float | None,
            soc: float | None,
            grid: float | None,
        ) -> bool:
            del pv, soc, grid
            return not relay_on

        def flush_overrides(now: float | None = None) -> None:
            del now

        def time_now() -> float:
            return 0.0

        runtime = SimpleNamespace(
            enqueue_dbus_publish_fields=enqueue_fields,
            update_worker_snapshot=update_snapshot,
            source_retry_ready=retry_ready,
            mark_recovery=lambda *_args: None,
            mark_failure=lambda *_args: None,
            delay_source_retry=lambda *_args: None,
            warning_throttled=lambda *_args: None,
            worker_snapshot=worker_snapshot,
        )
        auto = SimpleNamespace(
            mode_uses_auto_logic=mode_uses_auto,
            decide_relay=decide_relay,
        )
        state = SimpleNamespace(flush_runtime_overrides=flush_overrides)
        host = SimpleNamespace(
            runtime=runtime,
            auto=auto,
            state=state,
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_health_code=0,
            _last_health_reason="ok",
            last_status=0,
            started_at=0.0,
            virtual_set_current=6.0,
            auto_input_helper_restart_seconds=5.0,
            auto_input_helper_stale_seconds=10.0,
            auto_input_snapshot_path="/run/input.json",
            virtual_mode=1,
            _auto_input_helper_generation=1,
            _auto_input_runtime_instance_id="instance",
            dbus_gateway_cache_path="/run/cache.json",
            dbus_gateway_run_dir="/run",
            dbus_gateway_max_age_seconds=10.0,
            auto_dbus_backoff_base_seconds=1.0,
            auto_dbus_backoff_max_seconds=60.0,
            auto_pv_scan_interval_seconds=30.0,
            auto_pv_service="",
            auto_pv_service_prefix="com.victronenergy.pvinverter",
            auto_pv_max_services=8,
            auto_battery_scan_interval_seconds=30.0,
            auto_battery_service="",
            auto_battery_service_prefix="com.victronenergy.battery",
            auto_battery_soc_path="/Soc",
            auto_battery_capacity_wh=None,
            auto_battery_power_path="/Dc/0/Power",
            auto_battery_ac_power_path="/Ac/Power",
            auto_battery_pv_power_path="/Dc/Pv/Power",
            auto_battery_grid_interaction_path="/Ac/Grid/Power",
            auto_battery_operating_mode_path="/Settings/CGwacs/BatteryLife/State",
            auto_grid_service="com.victronenergy.system",
            auto_energy_sources=(),
            auto_use_combined_battery_soc=False,
            _last_dbus_ok_at=0.0,
            _last_pv_missing_warning=None,
            _dbus_list_backoff_until=0.0,
            _dbus_list_failures=0,
            _resolved_auto_pv_services=[],
            _auto_pv_last_scan=0.0,
            _resolved_auto_battery_service=None,
            _auto_battery_last_scan=0.0,
            _resolved_auto_energy_services={},
            _auto_energy_last_scan={},
            _last_energy_learning_profiles={},
            _last_energy_cluster={},
            service_name="com.victronenergy.evcharger.http_60",
            _readback_store=object(),
            time_now=time_now,
        )

        self.assertIs(require_publish_service(host), host)
        self.assertIs(require_auto_input_service(host), host)
        self.assertIs(require_dbus_input_service(host), host)
        self.assertIs(require_update_cycle_service(host), host)
        self.assertIs(require_backend_target(host), host)
        for requirement in (
            require_publish_service,
            require_auto_input_service,
            require_dbus_input_service,
            require_update_cycle_service,
            require_backend_target,
        ):
            with self.assertRaises(TypeError):
                requirement(1)

    def test_auto_facade_coordinates_decisions_commands_and_invalidations(self) -> None:
        owner, runtime_controller, write = _owner()
        async_enabled = False
        events: list[tuple[ControlCommand, ControlResult]] = []
        runtime = ServiceRuntimeFacade(owner)
        facade = ServiceAutoFacade(lambda: async_enabled, owner, runtime, lambda command, result: events.append((command, result)))

        self.assertTrue(facade.mode_uses_auto_logic(2))
        self.assertEqual(facade.normalize_mode("1"), 1)
        facade.clear_samples()
        facade.mark_relay_changed(True, 3.0)
        facade.set_health("ok", True)
        self.assertTrue(facade.decide_relay(False, 100.0, 80.0, -10.0))
        command = facade.command_from_write("/Mode", 1)
        payload_command = facade.command_from_payload({"path": "/Mode", "value": 2})
        self.assertEqual(payload_command.source, "http")
        self.assertTrue(facade.handle_command(command).accepted)
        self.assertTrue(facade.handle_dbus_write("/Mode", 1))
        write.accepted = False
        self.assertFalse(facade.handle_dbus_write("/Mode", 1))
        async_enabled = True
        self.assertTrue(facade.handle_dbus_write("/Mode", 1))
        self.assertEqual(runtime_controller.calls[-1][0], "enqueue_control_command")
        facade.invalidate_pv_services()
        facade.invalidate_battery_service()
        self.assertEqual(len(events), 3)

    def test_runtime_facade_exposes_the_typed_runtime_and_shelly_surface(self) -> None:
        owner, runtime_controller, _write = _owner()
        facade = ServiceRuntimeFacade(owner)
        session = _SessionDouble()
        command = ControlCommand(name="set_mode", path="/Mode", value=1)

        facade.reset_system_bus()
        with self.assertRaisesRegex(RuntimeError, "Direct DBus access"):
            facade.get_system_bus()
        facade.initialize_worker_state()
        facade.ensure_worker_state()
        facade.mark_mainloop_thread()
        self.assertTrue(facade.dbus_publish_direct_allowed())
        facade.assert_dbus_mainloop_thread("publish")
        self.assertTrue(facade.enqueue_dbus_publish_values([("/Mode", 1)], 1.0))
        self.assertTrue(facade.enqueue_dbus_publish_fields([("mode", 1)], 1.0))
        facade.enqueue_dbus_update_index_bump(1.0)
        self.assertTrue(facade.enqueue_companion_dbus_publish(2.0))
        self.assertTrue(facade.flush_dbus_publish_queue())
        facade.start_update_worker()
        self.assertTrue(facade.schedule_update_cycle())
        facade.start_control_command_worker()
        self.assertTrue(facade.enqueue_control_command(command))
        self.assertTrue(facade.mainloop_heartbeat_tick())
        facade.start_mainloop_watchdog()
        facade.update_worker_snapshot(heartbeat_at=2.0)
        self.assertEqual(facade.worker_snapshot()["heartbeat_at"], 12.0)
        facade.ensure_observability_state()
        self.assertFalse(facade.update_is_stale(3.0))
        facade.recover_watchdog(3.0)
        facade.warning_throttled("key", 2.0, "%s", "detail", exc_info=ValueError("x"))
        facade.write_auto_audit_event("auto", True)
        facade.mark_failure("pv")
        facade.mark_recovery("pv", "%s", "ok")
        self.assertTrue(facade.source_retry_ready("pv", 3.0))
        self.assertEqual(facade.source_retry_remaining("pv", 3.0), 4)
        facade.delay_source_retry("pv", 3.0)
        facade.delay_source_retry("pv", 3.0, 8.0)
        facade.stop_auto_input_helper(True)
        facade.spawn_auto_input_helper(3.0)
        facade.ensure_auto_input_helper(3.0)
        facade.refresh_auto_input_snapshot(3.0)
        self.assertEqual(facade.request("http://shelly")["url"], "http://shelly")
        self.assertEqual(facade.request_with_session(session, "http://shelly")["url"], "http://shelly")
        self.assertEqual(facade.rpc_call("Status", id=0)["method"], "Status")
        self.assertEqual(facade.rpc_call_with_session(session, "Status", id=0)["method"], "Status")
        self.assertTrue(facade.worker_fetch_pm_status().get("output"))
        self.assertTrue(facade.build_local_pm_status(True).get("output"))
        self.assertFalse(facade.publish_local_pm_status(False, 3.0).get("output"))
        facade.queue_relay_command(True, 3.0)
        self.assertEqual(facade.pending_relay_command(), (True, 9.0))
        facade.clear_pending_relay_command(True)
        facade.apply_pending_relay_command()
        facade.start_io_worker()
        self.assertFalse(facade.fetch_pm_status()["output"])
        self.assertTrue(facade.set_relay(True)["output"])
        self.assertTrue(facade.phase_selection_requires_pause())
        self.assertEqual(facade.apply_phase_selection("P1"), "P1")
        self.assertEqual(runtime_controller.calls[0][0], "reset_system_bus")

    def test_state_and_update_facades_cover_all_routes(self) -> None:
        owner, runtime_controller, _write = _owner()
        runtime = ServiceRuntimeFacade(owner)
        state = ServiceStateFacade(owner, runtime)
        update = ServiceUpdateFacade(owner)

        self.assertTrue(state.config_path().endswith("config.venus_evcharger.ini"))
        self.assertEqual(state.summary(), "ready")
        self.assertEqual(state.current(), {"mode": 2})
        state.load_runtime_state()
        state.save_runtime_state()
        state.save_runtime_overrides()
        state.flush_runtime_overrides(4.0)
        state.validate_runtime_config()
        self.assertIsInstance(state.load_config(), configparser.ConfigParser)
        state.ensure_publish_state()
        self.assertTrue(state.publish_field("mode", 2, 4.0, force=True))
        state.bump_update_index(4.0)
        phase_data = {"L1": {"power": 100.0, "voltage": 230.0, "current": 0.4}}
        self.assertTrue(state.publish_live_measurements(100.0, 230.0, 0.4, phase_data, 4.0))
        self.assertTrue(state.publish_energy_time_measurements(2.0, {"L1": 2.0}, 60, 1.0, 4.0))
        self.assertTrue(state.publish_config_paths(1, 4.0))
        self.assertTrue(state.publish_diagnostic_paths(4.0))
        state.start_companion_bridge()
        state.stop_companion_bridge()
        runtime_controller.direct_publish = False
        self.assertTrue(state.publish_companion_bridge(4.0))
        runtime_controller.direct_publish = True
        self.assertTrue(state.publish_companion_bridge(5.0))
        self.assertTrue(update.update())
        self.assertTrue(update.sign_of_life())

    def test_controller_owner_enforces_lifecycle_and_installs_backends(self) -> None:
        service = SimpleNamespace()
        runtime = _RuntimeDouble()
        auto = _AutoDouble()
        publisher = _PublisherDouble()
        shelly = _ShellyDouble()
        write = _WriteDouble()
        auto_input = _AutoInputDouble()
        dbus_input = _DbusInputDouble()
        update = _UpdateDouble()
        companion = _CompanionDouble()
        state = _StateDouble()
        bootstrap = _BootstrapDouble()
        backend_summary = SimpleNamespace(topology_configured=True, primary_rpc_configured=False)
        resolved = SimpleNamespace(runtime=backend_summary, meter="meter", switch="switch", charger="charger")

        constructor_values: dict[str, object] = {
            "ServiceStateController": state,
            "ServiceBootstrapController": bootstrap,
            "RuntimeSupportController": runtime,
            "AutoDecisionController": auto,
            "DbusPublishController": publisher,
            "ShellyIoController": shelly,
            "DbusWriteController": write,
            "AutoInputSupervisor": auto_input,
            "DbusInputController": dbus_input,
            "UpdateCycleController": update,
            "EnergyCompanionDbusBridge": companion,
        }

        def factory(value: object) -> Callable[..., object]:
            return lambda *args, **kwargs: value

        patches = [patch.object(owner_module, name, factory(value)) for name, value in constructor_values.items()]
        def identity(value: object) -> object:
            return value

        def resolved_backends(value: object) -> object:
            del value
            return resolved

        patches.extend(
            [
                patch.object(owner_module, "require_publish_service", identity),
                patch.object(owner_module, "require_auto_input_service", identity),
                patch.object(owner_module, "require_dbus_input_service", identity),
                patch.object(owner_module, "require_update_cycle_service", identity),
                patch.object(owner_module, "build_service_backends", resolved_backends),
            ]
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        owner = ServiceControllerOwner(service, _functions())
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            _ = owner.runtime
        controllers = owner.initialize_runtime()
        self.assertIs(controllers.runtime, runtime)
        self.assertIs(owner.runtime, controllers)
        self.assertEqual(
            owner._phase_values(10.0, 230.0, "L2", "phase"),
            {"L2": {"power": 10.0, "voltage": 230.0, "current": 1.0, "mode": 5.0}},
        )
        self.assertEqual(service._backend_bundle, resolved)
        self.assertEqual(service._meter_backend, "meter")
        self.assertEqual(service._switch_backend, "switch")
        self.assertEqual(service._charger_backend, "charger")
        self.assertTrue(service.topology_configured)
        self.assertFalse(service.primary_rpc_configured)
        self.assertEqual(
            [call[0] for call in runtime.calls[:2]],
            ["initialize_runtime_support", "init_worker_state"],
        )
        with self.assertRaisesRegex(RuntimeError, "already initialized"):
            owner.initialize_runtime()

        prepared_owner = ServiceControllerOwner(service, _functions())
        self.assertIs(prepared_owner.prepare_runtime_state(), runtime)
        with self.assertRaisesRegex(RuntimeError, "already prepared"):
            prepared_owner.prepare_runtime_state()
        self.assertIs(prepared_owner.initialize_runtime().runtime, runtime)


if __name__ == "__main__":
    unittest.main()
