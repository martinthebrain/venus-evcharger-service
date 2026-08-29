# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport clients for the dedicated DBus gateway process."""

from __future__ import annotations

import logging
import socket
from collections.abc import Mapping
from pathlib import Path

from venus_evcharger.dbus_gateway_cache import DbusCacheStore
from venus_evcharger.dbus_gateway_commands import DbusGatewayCommandInbox
from venus_evcharger.dbus_gateway_core import (
    GatewayPaths,
    _json_ready,
    _now,
    float_or_zero,
    gateway_paths,
    is_object_mapping,
    normalized_object_mapping,
)
from venus_evcharger.dbus_gateway_policy import command_allowed_by_backpressure
from venus_evcharger.ipc.command_mailbox import MailboxLockTimeout
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergyTopologySnapshot,
)
from venus_evcharger.ipc.energy_binary import load_energy_inputs_file
from venus_evcharger.ipc.enqueue_result import GatewayEnqueueFailure, GatewayEnqueueResult
from venus_evcharger.ipc.fast_publication import is_transient_publication
from venus_evcharger.ipc.fast_publication_wire import (
    FAST_PUBLICATION_WIRE_HEADER_BYTES,
    FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES,
    FastPublicationWireError,
    decode_fast_publication_frame,
    encode_fast_publication_frame,
    fast_publication_frame_size,
)
from venus_evcharger.ipc.gateway_operations import (
    ess_grid_setpoint_command,
    gx_relay_refresh_command,
    gx_relay_set_command,
    gx_relay_state_key,
)
from venus_evcharger.ipc.gateway_pressure import read_gateway_pressure_snapshot
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


def _is_live_transient_publication(command: CommandMapping) -> bool:
    """Keep bounded live GUI data flowing while advisory work is throttled."""
    return (
        command.get("publication_priority") == "live"
        and is_transient_publication(command)
    )


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
        self._backpressure_cache: tuple[float, str] = (0.0, "slow")
        self._publication_orders = publication_order_issuer or PublicationOrderIssuer()

    def _send_fast_publication(self, payload: CommandMapping) -> CommandPayload:
        try:
            ready = _json_ready(dict(payload))
            ready_payload = normalized_object_mapping(ready)
            if ready_payload is None:
                raise FastPublicationWireError("payload-must-be-object")
            frame = encode_fast_publication_frame(ready_payload)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_seconds)
                sock.connect(self.paths.socket_path)
                sock.sendall(frame)
                return _receive_fast_response(sock)
        except (FastPublicationWireError, OSError, RuntimeError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    def enqueue_command(self, command: CommandMapping) -> GatewayEnqueueResult:
        ordered_command = self._ordered_publication(command)
        fast_result = self._enqueue_live_before_backpressure(ordered_command)
        if fast_result is not None:
            return fast_result
        if not command_allowed_by_backpressure(ordered_command, self.backpressure_state(max_age_seconds=2.0)):
            return GatewayEnqueueResult(False, reason="backpressure")
        return self._enqueue_admitted_command(ordered_command)

    def _enqueue_live_before_backpressure(
        self,
        command: CommandMapping,
    ) -> GatewayEnqueueResult | None:
        if not _is_live_transient_publication(command):
            return None
        return self._enqueue_fast(command)

    def _enqueue_admitted_command(self, command: CommandMapping) -> GatewayEnqueueResult:
        if not is_transient_publication(command):
            return self._enqueue_durable_command(command)
        return self._enqueue_fast(command) or self._enqueue_durable_command(command)

    def _enqueue_fast(self, command: CommandMapping) -> GatewayEnqueueResult | None:
        command_id = _accepted_fast_command_id(self._send_fast_publication(command))
        if not command_id:
            return None
        return GatewayEnqueueResult(True, command_id, "socket")

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

    def _durable_enqueue_failed(self, reason: GatewayEnqueueFailure) -> GatewayEnqueueResult:
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
        state = read_gateway_pressure_snapshot(
            self.paths.health_path,
            now=now,
            max_age_seconds=max_age_seconds,
        ).state
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
        result = self._client.enqueue_command(gx_relay_refresh_command(relay_index))
        if not result.accepted:
            logging.warning(
                "Gateway rejected GX relay refresh for relay %d: %s",
                relay_index,
                result.reason or "unknown",
            )
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
    return (
        cached_at > 0.0
        and cached_at <= now
        and now - cached_at < 1.0
        and bool(cached_state)
    )


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


def _receive_fast_response(sock: socket.socket) -> CommandPayload:
    received = bytearray()
    expected_size = 0
    maximum_size = FAST_PUBLICATION_WIRE_HEADER_BYTES + FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES
    while expected_size == 0 or len(received) < expected_size:
        received.extend(_receive_response_chunk(sock, maximum_size, len(received)))
        expected_size = fast_publication_frame_size(received)
        if _response_exceeds_bounds(received, expected_size, maximum_size):
            raise FastPublicationWireError("response-too-large")
    return decode_fast_publication_frame(bytes(received))


def _receive_response_chunk(
    sock: socket.socket,
    maximum_size: int,
    received_size: int,
) -> bytes:
    chunk = sock.recv(min(4096, maximum_size + 1 - received_size))
    if not chunk:
        raise FastPublicationWireError("response-incomplete")
    return chunk


def _response_exceeds_bounds(
    received: bytearray,
    expected_size: int,
    maximum_size: int,
) -> bool:
    return len(received) > maximum_size or (
        expected_size > 0 and len(received) > expected_size
    )


def _binary_integer_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and int(value) in (0, 1):
        return int(value)
    return None
