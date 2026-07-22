# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport clients for the dedicated DBus gateway process."""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from pathlib import Path

from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_gateway_cache import DbusCacheStore
from venus_evcharger.dbus_gateway_commands import DbusGatewayCommandInbox
from venus_evcharger.dbus_gateway_core import (
    GatewayPaths,
    _json_ready,
    _now,
    float_or_zero,
    gateway_paths,
    is_object_mapping,
)
from venus_evcharger.dbus_gateway_policy import command_allowed_by_backpressure
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergyTopologySnapshot,
)
from venus_evcharger.ipc.gateway_operations import (
    ess_grid_setpoint_command,
    gx_relay_refresh_command,
    gx_relay_set_command,
    gx_relay_state_key,
)
from venus_evcharger.ipc.gateway_publication import (
    publish_companion_fields_command,
    publish_evcs_fields_command,
    register_companion_command,
    register_evcs_command,
)
from venus_evcharger.ipc.generic_shelly_configuration import (
    disable_matching_generic_shelly_once_command,
)
from venus_evcharger.ports.gateway_operations import (
    EssSetpointIntent,
    GatewayOperationReceipt,
    GxRelaySetRequest,
)
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    EvcsServiceIdentity,
    PublicationPriority,
    PublicationReceipt,
)
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyConfigurationReceipt,
)


class GatewayClient:
    """Small Unix-socket and command-file client used by non-DBus processes."""

    def __init__(self, paths: GatewayPaths | None = None, *, timeout_seconds: float = 0.5) -> None:
        self.paths = paths or gateway_paths()
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.commands = DbusGatewayCommandInbox(self.paths.command_dir)
        self._backpressure_cache: tuple[float, str] = (0.0, "unknown")

    def send(self, payload: CommandMapping) -> CommandPayload:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_seconds)
                sock.connect(self.paths.socket_path)
                sock.sendall((compact_json(_json_ready(dict(payload))) + "\n").encode())
                data = sock.recv(65536)
            if not data:
                return {"ok": True}
            response: object = json.loads(data)
            if not is_object_mapping(response):
                return {"ok": False, "error": "invalid-response"}
            return {str(key): value for key, value in response.items()}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    def enqueue_command(self, command: CommandMapping) -> str:
        if not command_allowed_by_backpressure(command, self.backpressure_state(max_age_seconds=2.0)):
            return ""
        return self.commands.enqueue(command)

    def request_energy_refresh(self, request: EnergyRefreshRequest, *, source: str) -> str:
        return self.enqueue_command(request.to_command(source=source))

    def load_cache(self, *, max_age_seconds: float = 10.0) -> CommandPayload:
        return DbusCacheStore.load_snapshot(self.paths.cache_path, max_age_seconds=max_age_seconds)

    def load_energy_inputs(self, *, max_age_seconds: float = 10.0) -> EnergyInputsSnapshot | None:
        payload = self.load_cache(max_age_seconds=max_age_seconds).get("energy_inputs")
        try:
            return EnergyInputsSnapshot.from_payload(payload)
        except (KeyError, TypeError, ValueError):
            return None

    def load_energy_topology(self, *, max_age_seconds: float = 30.0) -> EnergyTopologySnapshot | None:
        payload = self.load_cache(max_age_seconds=max_age_seconds).get("energy_topology")
        try:
            return EnergyTopologySnapshot.from_payload(payload)
        except (KeyError, TypeError, ValueError):
            return None

    def load_health(self, *, max_age_seconds: float = 10.0) -> CommandPayload:
        payload = DbusCacheStore.load_snapshot(self.paths.health_path, max_age_seconds=max_age_seconds)
        health = payload.get("dbus_health")
        return {str(key): value for key, value in health.items()} if is_object_mapping(health) else payload

    def backpressure_state(self, *, max_age_seconds: float = 10.0) -> str:
        cached_at, cached_state = self._backpressure_cache
        now = _now()
        if _backpressure_cache_fresh(cached_at, cached_state, now):
            return cached_state
        health = self.load_health(max_age_seconds=max_age_seconds)
        state = _backpressure_state_from_health(health)
        self._backpressure_cache = (now, state)
        return state


class GatewayOperationsClient:
    """Typed semantic operation facade over the gateway command transport."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def read_gx_relay_state(
        self,
        relay_index: int,
        *,
        max_age_seconds: float,
    ) -> int | None:
        key = gx_relay_state_key(relay_index)
        value = gateway_value(
            self._client.load_cache(max_age_seconds=max_age_seconds),
            key,
            max_age_seconds=max_age_seconds,
        )
        state = _binary_integer_or_none(value)
        if state is not None:
            return state
        self._client.enqueue_command(gx_relay_refresh_command(relay_index))
        return None

    def set_gx_relay_enabled(
        self,
        request: GxRelaySetRequest,
    ) -> GatewayOperationReceipt:
        command_path = self._client.enqueue_command(
            gx_relay_set_command(
                request.relay_index,
                request.contact_mode,
                request.enabled,
                ensure_manual=request.ensure_manual,
                verify_settle_seconds=request.verify_settle_seconds,
                verify_retry_seconds=request.verify_retry_seconds,
            )
        )
        return _operation_receipt(command_path)

    def set_ess_grid_setpoint(
        self,
        watts: float,
        *,
        intent: EssSetpointIntent,
    ) -> GatewayOperationReceipt:
        command_path = self._client.enqueue_command(ess_grid_setpoint_command(watts, intent=intent))
        return _operation_receipt(command_path)


class GatewayPublicationClient:
    """Typed publication port backed by the gateway command mailbox."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def register_evcs(
        self,
        identity: EvcsServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        return self._enqueue(register_evcs_command(identity, initial_fields))

    def publish_evcs_fields(
        self,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        return self._enqueue(publish_evcs_fields_command(fields, priority=priority))

    def register_companion(
        self,
        identity: CompanionServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        return self._enqueue(register_companion_command(identity, initial_fields))

    def publish_companion_fields(
        self,
        service_id: str,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        return self._enqueue(publish_companion_fields_command(service_id, fields, priority=priority))

    def _enqueue(self, command: CommandMapping) -> PublicationReceipt:
        command_path = self._client.enqueue_command(command)
        return PublicationReceipt(
            accepted=bool(command_path),
            command_id=Path(command_path).stem if command_path else "",
        )


class GatewayGenericShellyConfigurationClient:
    """Submit generic Shelly configuration intents to the gateway mailbox."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def disable_matching_device_channel_once(
        self,
        request: DisableMatchingGenericShellyOnceRequest,
    ) -> GenericShellyConfigurationReceipt:
        command_path = self._client.enqueue_command(
            disable_matching_generic_shelly_once_command(request)
        )
        if not command_path:
            return GenericShellyConfigurationReceipt(
                accepted=False,
                reason="gateway did not accept the configuration command",
            )
        return GenericShellyConfigurationReceipt(
            accepted=True,
            command_id=Path(command_path).stem,
        )


def gateway_value(snapshot: CommandMapping, key: str, *, max_age_seconds: float) -> object:
    entry = DbusCacheStore.value_entry(snapshot, key)
    if entry is None:
        return None
    status = entry.get("status")
    if status != "fresh" and status != "stale":
        return None
    if float_or_zero(entry.get("age_s")) > float(max_age_seconds):
        return None
    return entry.get("value")


def _backpressure_cache_fresh(cached_at: float, cached_state: str, now: float) -> bool:
    return now - cached_at < 1.0 and cached_state != "unknown"


def _backpressure_state_from_health(health: CommandMapping) -> str:
    backpressure = health.get("backpressure")
    if not is_object_mapping(backpressure):
        return "unknown"
    state = backpressure.get("state")
    return str(state) if state else "unknown"


def _operation_receipt(command_path: str) -> GatewayOperationReceipt:
    return GatewayOperationReceipt(
        accepted=bool(command_path),
        command_id=Path(command_path).stem if command_path else "",
    )


def _binary_integer_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and int(value) in (0, 1):
        return int(value)
    return None
