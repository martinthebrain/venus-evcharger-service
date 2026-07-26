# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport clients for the dedicated DBus gateway process."""

from __future__ import annotations

import json
import logging
import socket
from collections import Counter
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
from venus_evcharger.ipc.command_mailbox import MailboxLockTimeout, normalized_mapping
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergyTopologySnapshot,
)
from venus_evcharger.ipc.energy_binary import load_energy_inputs_file
from venus_evcharger.ipc.enqueue_result import GatewayEnqueueFailure, GatewayEnqueueResult
from venus_evcharger.ipc.fast_publication import is_transient_publication
from venus_evcharger.ipc.gateway_operations import (
    ess_grid_setpoint_command,
    gx_relay_refresh_command,
    gx_relay_set_command,
    gx_relay_state_key,
)
from venus_evcharger.ipc.gateway_publication import (
    SEMANTIC_PUBLICATION_KINDS,
    publish_companion_fields_command,
    publish_evcs_fields_command,
    register_companion_command,
    register_evcs_command,
)
from venus_evcharger.ipc.generic_shelly_configuration import (
    disable_matching_generic_shelly_once_command,
)
from venus_evcharger.ipc.publication_order import PublicationOrderIssuer
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


def _energy_topology_or_none(payload: object) -> EnergyTopologySnapshot | None:
    """Validate one topology payload without leaking transport errors."""
    try:
        return EnergyTopologySnapshot.from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return None


class GatewayClient:
    """Small Unix-socket and command-file client used by non-DBus processes."""

    def __init__(
        self,
        paths: GatewayPaths | None = None,
        *,
        timeout_seconds: float = 0.5,
        publication_order_issuer: PublicationOrderIssuer | None = None,
    ) -> None:
        self.paths = paths or gateway_paths()
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.commands = DbusGatewayCommandInbox(self.paths.command_dir)
        self._backpressure_cache: tuple[float, str] = (0.0, "unknown")
        self._publication_orders = publication_order_issuer or PublicationOrderIssuer()
        self._durable_enqueue_failures: Counter[GatewayEnqueueFailure] = Counter()

    def send(self, payload: CommandMapping) -> CommandPayload:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_seconds)
                sock.connect(self.paths.socket_path)
                sock.sendall((compact_json(_json_ready(dict(payload))) + "\n").encode())
                data = sock.recv(65536)
            if not data:
                return {"ok": True}
            decoded: object = json.loads(data)
            response = normalized_mapping(decoded)
            if response is None:
                return {"ok": False, "error": "invalid-response"}
            return response
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    def enqueue_command(self, command: CommandMapping) -> GatewayEnqueueResult:
        ordered_command = self._ordered_publication(command)
        if not command_allowed_by_backpressure(ordered_command, self.backpressure_state(max_age_seconds=2.0)):
            return GatewayEnqueueResult(False, reason="backpressure")
        if is_transient_publication(ordered_command):
            command_id = _accepted_fast_command_id(self.send(ordered_command))
            if command_id:
                return GatewayEnqueueResult(True, command_id, "socket")
        return self._enqueue_durable_command(ordered_command)

    def _enqueue_durable_command(
        self,
        command: CommandMapping,
    ) -> GatewayEnqueueResult:
        try:
            command_path = str(self.commands.enqueue(command))
            return GatewayEnqueueResult(
                True,
                Path(command_path).stem,
                "mailbox",
                command_path=command_path,
            )
        except MailboxLockTimeout:
            return self._durable_enqueue_failed("mailbox-lock-timeout")
        except ValueError:
            return self._durable_enqueue_failed("invalid-command")

    def enqueue_health(self) -> CommandPayload:
        """Expose process-local durable enqueue failures to diagnostics."""
        return {
            "durable_enqueue_failures": sum(self._durable_enqueue_failures.values()),
            "durable_enqueue_failures_by_reason": dict(sorted(self._durable_enqueue_failures.items())),
        }

    def _durable_enqueue_failed(self, reason: GatewayEnqueueFailure) -> GatewayEnqueueResult:
        self._durable_enqueue_failures[reason] += 1
        logging.error("Durable gateway command was not accepted: %s", reason)
        return GatewayEnqueueResult(False, reason=reason)

    def _ordered_publication(self, command: CommandMapping) -> CommandPayload:
        kind = command.get("kind")
        return self._publication_orders.ordered(command) if kind in SEMANTIC_PUBLICATION_KINDS else dict(command)

    def request_energy_refresh(
        self,
        request: EnergyRefreshRequest,
        *,
        source: str,
    ) -> GatewayEnqueueResult:
        return self.enqueue_command(request.to_command(source=source))

    def load_cache(self, *, max_age_seconds: float = 10.0) -> CommandPayload:
        return DbusCacheStore.load_snapshot(self.paths.cache_path, max_age_seconds=max_age_seconds)

    def load_energy_inputs(self, *, max_age_seconds: float = 10.0) -> EnergyInputsSnapshot | None:
        compact_snapshot = load_energy_inputs_file(
            self.paths.energy_inputs_path,
            max_age_seconds=max_age_seconds,
        )
        if compact_snapshot is not None:
            return compact_snapshot
        payload = self.load_cache(max_age_seconds=max_age_seconds).get("energy_inputs")
        try:
            return EnergyInputsSnapshot.from_payload(payload)
        except (KeyError, TypeError, ValueError):
            return None

    def load_energy_topology(self, *, max_age_seconds: float = 30.0) -> EnergyTopologySnapshot | None:
        """Load versioned topology while using the small health file as its heartbeat."""
        payload: object = DbusCacheStore.load_snapshot(
            self.paths.energy_topology_path,
            max_age_seconds=-1.0,
        )
        snapshot = _energy_topology_or_none(payload)
        if snapshot is None:
            return None
        return snapshot if self.load_health(max_age_seconds=max_age_seconds) else None

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
        result = self._client.enqueue_command(
            gx_relay_set_command(
                request.relay_index,
                request.contact_mode,
                request.enabled,
                ensure_manual=request.ensure_manual,
                verify_settle_seconds=request.verify_settle_seconds,
                verify_retry_seconds=request.verify_retry_seconds,
            )
        )
        return _operation_receipt(result)

    def set_ess_grid_setpoint(
        self,
        watts: float,
        *,
        intent: EssSetpointIntent,
    ) -> GatewayOperationReceipt:
        result = self._client.enqueue_command(ess_grid_setpoint_command(watts, intent=intent))
        return _operation_receipt(result)


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
        result = self._client.enqueue_command(command)
        return PublicationReceipt(
            accepted=result.accepted,
            command_id=result.command_id,
            reason=result.reason,
        )


class GatewayGenericShellyConfigurationClient:
    """Submit generic Shelly configuration intents to the gateway mailbox."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def disable_matching_device_channel_once(
        self,
        request: DisableMatchingGenericShellyOnceRequest,
    ) -> GenericShellyConfigurationReceipt:
        result = self._client.enqueue_command(
            disable_matching_generic_shelly_once_command(request)
        )
        if not result.accepted:
            return GenericShellyConfigurationReceipt(
                accepted=False,
                reason=result.reason or "gateway did not accept the configuration command",
            )
        return GenericShellyConfigurationReceipt(
            accepted=True,
            command_id=result.command_id,
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


def _operation_receipt(result: GatewayEnqueueResult) -> GatewayOperationReceipt:
    return GatewayOperationReceipt(
        accepted=result.accepted,
        command_id=result.command_id,
        reason=result.reason,
    )


def _accepted_fast_command_id(response: CommandMapping) -> str:
    if response.get("ok") is not True or response.get("accepted") is not True:
        return ""
    command_id = response.get("command_id")
    return str(command_id) if isinstance(command_id, str) and command_id else ""


def _binary_integer_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and int(value) in (0, 1):
        return int(value)
    return None
