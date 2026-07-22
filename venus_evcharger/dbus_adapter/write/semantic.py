# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-owned mappings for semantic system operations."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import dbus

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred
from venus_evcharger.dbus_adapter.write.generic_shelly import GenericShellyConfigurationExecutor
from venus_evcharger.dbus_adapter.write.protocols import DbusWriteSchedulerAdapter
from venus_evcharger.dbus_adapter.write.publish import DbusWriteSchedulerPublish
from venus_evcharger.dbus_gateway import dbus_path_key
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_operations import (
    ESS_GRID_SETPOINT_KIND,
    GX_RELAY_REFRESH_KIND,
    GX_RELAY_SET_KIND,
    GxRelaySetOperation,
    RelayPhase,
    gx_relay_state_key,
    parse_ess_grid_setpoint,
    parse_gx_relay_refresh,
    parse_gx_relay_set,
)
from venus_evcharger.ipc.generic_shelly_configuration import DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND

_SYSTEM_SERVICE = "com.victronenergy.system"
_SETTINGS_SERVICE = "com.victronenergy.settings"
_ESS_DEFAULT_PATH = "/Settings/CGwacs/AcPowerSetPoint"
_ESS_SERVICE_CONFIG_KEY = "AutoBatteryDischargeBalanceVictronBiasService"
_ESS_PATH_CONFIG_KEY = "AutoBatteryDischargeBalanceVictronBiasPath"
_MANUAL_FUNCTION_VALUE = 2
_DBUS_TIMEOUT_SECONDS = 1.0


class DbusWriteSchedulerSemantic(DbusWriteSchedulerPublish):
    """Execute semantic commands without exposing DBus topology to callers."""

    adapter: DbusWriteSchedulerAdapter

    def process_semantic_operation(
        self,
        command: CommandMapping,
        *,
        command_file: str,
    ) -> CommandOutcome:
        kind = str(command.get("kind"))
        handlers: dict[str, Callable[[], CommandOutcome]] = {
            GX_RELAY_REFRESH_KIND: lambda: self._refresh_gx_relay(command),
            GX_RELAY_SET_KIND: lambda: self._set_gx_relay(command, command_file),
            ESS_GRID_SETPOINT_KIND: lambda: self._set_ess_grid_setpoint(command),
            DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND: lambda: GenericShellyConfigurationExecutor(
                self.adapter
            ).process(command, command_file),
        }
        handler = handlers.get(kind)
        return "dropped" if handler is None else handler()

    def _refresh_gx_relay(self, command: CommandMapping) -> CommandOutcome:
        operation = parse_gx_relay_refresh(command)
        if operation is None:
            return "dropped"
        path = self._relay_state_path(operation.relay_index)
        state = self._read_binary_value(_SYSTEM_SERVICE, path)
        if state is None:
            self._mark_relay_error(operation.relay_index, path, "relay state is not binary")
            return "dropped"
        self._cache_relay_state(operation.relay_index, path, state)
        return "applied"

    def _set_gx_relay(self, command: CommandMapping, command_file: str) -> CommandOutcome:
        operation = parse_gx_relay_set(command)
        if operation is None or not command_file:
            return "dropped"
        handlers: dict[
            RelayPhase,
            Callable[[CommandMapping, str, GxRelaySetOperation], CommandOutcome],
        ] = {
            "manual_read": self._read_manual_function,
            "manual_write": self._write_manual_function,
            "output": self._write_relay_output,
            "verify": self._verify_relay_output,
            "retry": self._retry_relay_output,
        }
        return handlers[operation.phase](command, command_file, operation)

    def _read_manual_function(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
    ) -> CommandOutcome:
        path = self._manual_function_paths(operation.relay_index)[operation.manual_target]
        try:
            value = self._read_value(_SETTINGS_SERVICE, path)
        except DbusOperationDeferred:
            raise
        except DBUS_GATEWAY_OPERATION_ERRORS:
            return self._manual_fallback(command, command_file, operation)
        next_phase = "output" if coerce_dbus_numeric(value) == _MANUAL_FUNCTION_VALUE else "manual_write"
        return self._rewrite(command_file, command, phase=next_phase)

    def _write_manual_function(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
    ) -> CommandOutcome:
        path = self._manual_function_paths(operation.relay_index)[operation.manual_target]
        try:
            self._write_value(_SETTINGS_SERVICE, path, _MANUAL_FUNCTION_VALUE)
        except DbusOperationDeferred:
            raise
        except DBUS_GATEWAY_OPERATION_ERRORS:
            return self._manual_fallback(command, command_file, operation)
        return self._rewrite(command_file, command, phase="output")

    def _manual_fallback(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
    ) -> CommandOutcome:
        candidates = self._manual_function_paths(operation.relay_index)
        next_target = operation.manual_target + 1
        if next_target >= len(candidates):
            logging.warning(
                "Deferring GX relay %s because no manual-function target is currently reachable",
                operation.relay_index,
            )
            return self._rewrite(
                command_file,
                command,
                not_before=time.time() + max(1.0, operation.verify_retry_seconds),
            )
        logging.debug("Trying legacy manual-function target for GX relay %s", operation.relay_index)
        return self._rewrite(
            command_file,
            command,
            phase="manual_read",
            manual_target=next_target,
        )

    def _write_relay_output(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
    ) -> CommandOutcome:
        path = self._relay_state_path(operation.relay_index)
        self._write_value(_SYSTEM_SERVICE, path, operation.target_state)
        return self._rewrite(
            command_file,
            command,
            phase="verify",
            not_before=time.time() + operation.verify_settle_seconds,
        )

    def _retry_relay_output(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
    ) -> CommandOutcome:
        path = self._relay_state_path(operation.relay_index)
        self._write_value(_SYSTEM_SERVICE, path, operation.target_state)
        return self._rewrite(
            command_file,
            command,
            phase="verify",
            retries=operation.retries + 1,
            not_before=time.time() + operation.verify_settle_seconds,
        )

    def _verify_relay_output(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
    ) -> CommandOutcome:
        path = self._relay_state_path(operation.relay_index)
        try:
            state = self._read_binary_value(_SYSTEM_SERVICE, path)
        except DbusOperationDeferred:
            raise
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            self._mark_relay_error(operation.relay_index, path, error)
            return "applied"
        if self._relay_state_matches(state, operation.target_state):
            self._cache_relay_state(operation.relay_index, path, operation.target_state)
            return "applied"
        return self._relay_mismatch_outcome(command, command_file, operation, path, state)

    def _relay_mismatch_outcome(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
        path: str,
        state: int | None,
    ) -> CommandOutcome:
        if operation.retries == 0:
            return self._rewrite(
                command_file,
                command,
                phase="retry",
                not_before=time.time() + operation.verify_retry_seconds,
            )
        self._mark_relay_error(
            operation.relay_index,
            path,
            f"relay stayed at {state}, expected {operation.target_state}",
        )
        return "dropped"

    @staticmethod
    def _relay_state_matches(state: int | None, target_state: int) -> bool:
        return state is not None and state == target_state

    def _set_ess_grid_setpoint(self, command: CommandMapping) -> CommandOutcome:
        operation = parse_ess_grid_setpoint(command)
        if operation is None:
            return "dropped"
        service, path = self._ess_grid_setpoint_target()
        if not service or not path:
            logging.warning("Dropping ESS setpoint operation because its adapter target is disabled")
            return "dropped"
        self._write_value(service, path, operation.watts)
        self.adapter.cache.update_value(
            dbus_path_key(service, path),
            operation.watts,
            source=f"{service}{path}",
            confidence=0.9,
            freshness_kind="external_read",
        )
        return "applied"

    def _read_value(self, service: str, path: str) -> object:
        return self.adapter.timed_dbus_operation("read", lambda: self._read_value_now(service, path))

    def _read_binary_value(self, service: str, path: str) -> int | None:
        value = coerce_dbus_numeric(self._read_value(service, path))
        return int(value) if isinstance(value, (int, float)) and value in (0, 1) else None

    def _read_value_now(self, service: str, path: str) -> object:
        obj = self.adapter.connection.get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "com.victronenergy.BusItem")
        return iface.GetValue(timeout=_DBUS_TIMEOUT_SECONDS)

    def _write_value(self, service: str, path: str, value: object) -> None:
        self.adapter.timed_dbus_operation(
            "write",
            lambda: self._write_value_now(service, path, value),
        )

    def _write_value_now(self, service: str, path: str, value: object) -> None:
        obj = self.adapter.connection.get_object(service, path, introspect=False)
        iface = dbus.Interface(obj, "com.victronenergy.BusItem")
        iface.SetValue(value, timeout=_DBUS_TIMEOUT_SECONDS)

    def _cache_relay_state(self, relay_index: int, path: str, state: int) -> None:
        self.adapter.cache.update_external_read(
            gx_relay_state_key(relay_index),
            state,
            source=f"{_SYSTEM_SERVICE}{path}",
            confidence=1.0,
        )

    def _mark_relay_error(self, relay_index: int, path: str, error: BaseException | str) -> None:
        self.adapter.cache.mark_error(
            gx_relay_state_key(relay_index),
            source=f"{_SYSTEM_SERVICE}{path}",
            error=error,
            freshness_kind="external_read",
        )

    def _ess_grid_setpoint_target(self) -> tuple[str, str]:
        defaults = self.adapter.config["DEFAULT"]
        service = str(defaults.get(_ESS_SERVICE_CONFIG_KEY, _SETTINGS_SERVICE)).strip()
        path = str(defaults.get(_ESS_PATH_CONFIG_KEY, _ESS_DEFAULT_PATH)).strip()
        return service, path

    def _rewrite(self, command_file: str, command: CommandMapping, **changes: object) -> CommandOutcome:
        self.adapter.json_writer.write(command_file, {**dict(command), **changes})
        return "deferred"

    @staticmethod
    def _relay_state_path(relay_index: int) -> str:
        return f"/Relay/{relay_index}/State"

    @staticmethod
    def _manual_function_paths(relay_index: int) -> tuple[str, ...]:
        primary = f"/Settings/Relay/{relay_index}/Function"
        return (primary, "/Settings/Relay/Function") if relay_index == 0 else (primary,)
