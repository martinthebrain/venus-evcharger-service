# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-owned mappings for semantic system operations."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall, dbus_call_operation
from venus_evcharger.dbus_adapter.contracts import (
    CommandCompletion,
    CommandExecution,
    CommandOutcome,
)
from venus_evcharger.dbus_adapter.write.busitem_calls import busitem_read_call, busitem_write_call
from venus_evcharger.dbus_adapter.write.generic_shelly import GenericShellyConfigurationExecutor
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.dbus_adapter.write.relay_topology import (
    MANUAL_FUNCTION_VALUE,
    SETTINGS_SERVICE,
    SYSTEM_SERVICE,
    binary_relay_state,
    manual_function_paths,
    manual_function_selected,
    relay_state_matches,
    relay_state_path,
)
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

_ESS_DEFAULT_PATH = "/Settings/CGwacs/AcPowerSetPoint"
_ESS_SERVICE_CONFIG_KEY = "AutoBatteryDischargeBalanceVictronBiasService"
_ESS_PATH_CONFIG_KEY = "AutoBatteryDischargeBalanceVictronBiasPath"


class SemanticWriteExecutor:
    """Execute semantic commands without exposing DBus topology to callers."""

    def __init__(self, adapter: SemanticWriteAdapter) -> None:
        self.adapter = adapter

    def schedule_semantic_operation(
        self,
        command: CommandMapping,
        *,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        kind = str(command.get("kind"))
        handlers: dict[str, Callable[[], CommandExecution]] = {
            GX_RELAY_REFRESH_KIND: lambda: self._refresh_gx_relay(
                command,
                command_file,
                completion,
            ),
            GX_RELAY_SET_KIND: lambda: self._set_gx_relay(
                command,
                command_file,
                completion,
            ),
            ESS_GRID_SETPOINT_KIND: lambda: self._set_ess_grid_setpoint(
                command,
                command_file,
                completion,
            ),
            DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND: lambda: GenericShellyConfigurationExecutor(
                self.adapter
            ).schedule(command, command_file, completion),
        }
        handler = handlers.get(kind)
        return CommandExecution.immediate("dropped") if handler is None else handler()

    def _refresh_gx_relay(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        operation = parse_gx_relay_refresh(command)
        if operation is None:
            return CommandExecution.immediate("dropped")
        path = relay_state_path(operation.relay_index)
        return self._schedule_read(
            busitem_read_call(
                SYSTEM_SERVICE,
                path,
                self._priority(command),
                command_file,
            ),
            on_success=lambda value: completion(self._refresh_relay_outcome(operation.relay_index, path, value)),
            on_error=lambda _error: completion("deferred"),
        )

    def _refresh_relay_outcome(
        self,
        relay_index: int,
        path: str,
        value: object,
    ) -> CommandOutcome:
        state = binary_relay_state(value)
        if state is None:
            self._mark_relay_error(relay_index, path, "relay state is not binary")
            return "dropped"
        self._cache_relay_state(relay_index, path, state)
        return "applied"

    def _set_gx_relay(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        operation = parse_gx_relay_set(command)
        if operation is None or not command_file:
            return CommandExecution.immediate("dropped")
        handlers: dict[
            RelayPhase,
            Callable[
                [CommandMapping, str, GxRelaySetOperation, CommandCompletion],
                CommandExecution,
            ],
        ] = {
            "manual_read": self._read_manual_function,
            "manual_write": self._write_manual_function,
            "output": self._write_relay_output,
            "verify": self._verify_relay_output,
            "retry": self._retry_relay_output,
        }
        return handlers[operation.phase](command, command_file, operation, completion)

    def _read_manual_function(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
        completion: CommandCompletion,
    ) -> CommandExecution:
        path = manual_function_paths(operation.relay_index)[operation.manual_target]
        return self._schedule_read(
            busitem_read_call(
                SETTINGS_SERVICE,
                path,
                self._priority(command),
                command_file,
            ),
            on_success=lambda value: completion(
                self._manual_read_outcome(
                    command,
                    command_file,
                    value,
                )
            ),
            on_error=lambda _error: completion(self._manual_fallback(command, command_file, operation)),
        )

    def _manual_read_outcome(
        self,
        command: CommandMapping,
        command_file: str,
        value: object,
    ) -> CommandOutcome:
        next_phase = "output" if manual_function_selected(value) else "manual_write"
        return self._rewrite(command_file, command, phase=next_phase)

    def _write_manual_function(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
        completion: CommandCompletion,
    ) -> CommandExecution:
        path = manual_function_paths(operation.relay_index)[operation.manual_target]
        return self._schedule_write(
            busitem_write_call(
                SETTINGS_SERVICE,
                path,
                MANUAL_FUNCTION_VALUE,
                self._priority(command),
                command_file,
            ),
            on_success=lambda: completion(self._rewrite(command_file, command, phase="output")),
            on_error=lambda _error: completion(self._manual_fallback(command, command_file, operation)),
        )

    def _manual_fallback(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
    ) -> CommandOutcome:
        candidates = manual_function_paths(operation.relay_index)
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
        logging.debug(
            "Trying alternate Venus relay-function target for GX relay %s",
            operation.relay_index,
        )
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
        completion: CommandCompletion,
    ) -> CommandExecution:
        path = relay_state_path(operation.relay_index)
        return self._schedule_write(
            busitem_write_call(
                SYSTEM_SERVICE,
                path,
                operation.target_state,
                self._priority(command),
                command_file,
            ),
            on_success=lambda: completion(
                self._rewrite(
                    command_file,
                    command,
                    phase="verify",
                    not_before=time.time() + operation.verify_settle_seconds,
                )
            ),
            on_error=lambda _error: completion("deferred"),
        )

    def _retry_relay_output(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
        completion: CommandCompletion,
    ) -> CommandExecution:
        path = relay_state_path(operation.relay_index)
        return self._schedule_write(
            busitem_write_call(
                SYSTEM_SERVICE,
                path,
                operation.target_state,
                self._priority(command),
                command_file,
            ),
            on_success=lambda: completion(
                self._rewrite(
                    command_file,
                    command,
                    phase="verify",
                    retries=operation.retries + 1,
                    not_before=time.time() + operation.verify_settle_seconds,
                )
            ),
            on_error=lambda _error: completion("deferred"),
        )

    def _verify_relay_output(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
        completion: CommandCompletion,
    ) -> CommandExecution:
        path = relay_state_path(operation.relay_index)
        return self._schedule_read(
            busitem_read_call(
                SYSTEM_SERVICE,
                path,
                self._priority(command),
                command_file,
            ),
            on_success=lambda value: completion(
                self._verify_relay_outcome(
                    command,
                    command_file,
                    operation,
                    path,
                    value,
                )
            ),
            on_error=lambda error: completion(self._verify_relay_error(operation.relay_index, path, error)),
        )

    def _verify_relay_outcome(
        self,
        command: CommandMapping,
        command_file: str,
        operation: GxRelaySetOperation,
        path: str,
        value: object,
    ) -> CommandOutcome:
        state = binary_relay_state(value)
        if relay_state_matches(state, operation.target_state):
            self._cache_relay_state(operation.relay_index, path, operation.target_state)
            return "applied"
        return self._relay_mismatch_outcome(command, command_file, operation, path, state)

    def _verify_relay_error(
        self,
        relay_index: int,
        path: str,
        error: BaseException,
    ) -> CommandOutcome:
        self._mark_relay_error(relay_index, path, error)
        return "applied"

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

    def _set_ess_grid_setpoint(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        operation = parse_ess_grid_setpoint(command)
        if operation is None:
            return CommandExecution.immediate("dropped")
        service, path = self._ess_grid_setpoint_target()
        if not service or not path:
            logging.warning("Dropping ESS setpoint operation because its adapter target is disabled")
            return CommandExecution.immediate("dropped")
        return self._schedule_write(
            busitem_write_call(
                service,
                path,
                operation.watts,
                self._priority(command),
                command_file,
            ),
            on_success=lambda: completion(self._complete_ess_grid_setpoint(service, path, operation.watts)),
            on_error=lambda _error: completion("deferred"),
        )

    def _complete_ess_grid_setpoint(
        self,
        service: str,
        path: str,
        watts: float,
    ) -> CommandOutcome:
        self.adapter.cache.update_value(
            dbus_path_key(service, path),
            watts,
            source=f"{service}{path}",
            confidence=0.9,
            freshness_kind="external_read",
        )
        return "applied"

    def _schedule_read(
        self,
        call: DbusMethodCall,
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[BaseException], None],
    ) -> CommandExecution:
        self.adapter.operation_broker.submit(
            dbus_call_operation(
                self.adapter.connection,
                call,
                on_success=on_success,
                on_error=on_error,
            )
        )
        return CommandExecution.pending()

    def _schedule_write(
        self,
        call: DbusMethodCall,
        *,
        on_success: Callable[[], None],
        on_error: Callable[[BaseException], None],
    ) -> CommandExecution:
        self.adapter.operation_broker.submit(
            dbus_call_operation(
                self.adapter.connection,
                call,
                on_success=lambda _value: on_success(),
                on_error=on_error,
            )
        )
        return CommandExecution.pending()

    @staticmethod
    def _priority(command: CommandMapping) -> str:
        return str(command.get("priority") or "user")

    def _cache_relay_state(self, relay_index: int, path: str, state: int) -> None:
        self.adapter.cache.update_external_read(
            gx_relay_state_key(relay_index),
            state,
            source=f"{SYSTEM_SERVICE}{path}",
            confidence=1.0,
        )

    def _mark_relay_error(self, relay_index: int, path: str, error: BaseException | str) -> None:
        self.adapter.cache.mark_error(
            gx_relay_state_key(relay_index),
            source=f"{SYSTEM_SERVICE}{path}",
            error=error,
            freshness_kind="external_read",
        )

    def _ess_grid_setpoint_target(self) -> tuple[str, str]:
        defaults = self.adapter.config["DEFAULT"]
        service = str(defaults.get(_ESS_SERVICE_CONFIG_KEY, SETTINGS_SERVICE)).strip()
        path = str(defaults.get(_ESS_PATH_CONFIG_KEY, _ESS_DEFAULT_PATH)).strip()
        return service, path

    def _rewrite(self, command_file: str, command: CommandMapping, **changes: object) -> CommandOutcome:
        replaced = self.adapter.commands.replace_if_current(
            command_file,
            command,
            {**dict(command), **changes},
        )
        return "deferred" if replaced else "dropped"
