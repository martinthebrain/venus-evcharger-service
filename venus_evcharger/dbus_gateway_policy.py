# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway queue classes and backpressure policy."""

from __future__ import annotations

from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
    REGISTER_COMPANION_KIND,
    REGISTER_EVCS_KIND,
)
from venus_evcharger.ipc.generic_shelly_configuration import DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND

_STATIC_QUEUE_CLASSES = {
    REGISTER_EVCS_KIND: "startup/register",
    REGISTER_COMPANION_KIND: "startup/register",
    "refresh_energy_inputs": "read-fast",
    "introspect": "introspection",
    "gx_relay_refresh": "read-fast",
    "gx_relay_set_enabled": "remote-write",
    "ess_grid_setpoint": "remote-write",
    DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND: "configuration",
}


def _command_text(command: CommandMapping, key: str) -> str:
    return str(command.get(key) or "")


def command_queue_class(command: CommandMapping) -> str:
    kind = _command_kind(command)
    if _is_publish_command(kind):
        return _publish_queue_class(command)
    if kind == "refresh_energy_inputs":
        return _refresh_queue_class(command)
    return _STATIC_QUEUE_CLASSES.get(kind, "diagnostic")


def _publish_queue_class(command: CommandMapping) -> str:
    priority = _command_text(command, "publication_priority")
    return "gui-critical-publish" if priority == "critical" else "local-publish"


def _refresh_queue_class(command: CommandMapping) -> str:
    return "discovery" if _command_text(command, "scope") == "topology" else "read-fast"


def command_allowed_by_backpressure(command: CommandMapping, state: str) -> bool:
    normalized_state = _normalized_state(state)
    priority = _normalized_priority(command)
    queue_class = _effective_queue_class(command)
    return _backpressure_rule(normalized_state, priority, queue_class)


def _backpressure_rule(state: str, priority: str, queue_class: str) -> bool:
    if _is_startup_registration(queue_class):
        return True
    if state == "congested":
        return _allowed_when_congested(priority, queue_class)
    if state == "slow":
        return _allowed_when_slow(priority, queue_class)
    if state == "protective":
        return _allowed_when_protective(priority, queue_class)
    return True


def _allowed_when_congested(priority: str, queue_class: str) -> bool:
    return priority not in {"optional", "diagnostic"} and queue_class != "diagnostic"


def _allowed_when_slow(priority: str, queue_class: str) -> bool:
    return queue_class == "gui-critical-publish" or priority in {"safety", "user"}


def _allowed_when_protective(priority: str, queue_class: str) -> bool:
    if queue_class not in {
        "gui-critical-publish",
        "remote-write",
    }:
        return False
    return priority in {"safety", "user"}


def _command_kind(command: CommandMapping) -> str:
    return _command_text(command, "kind") or _command_text(command, "type")


def _is_publish_command(kind: str) -> bool:
    return kind in {PUBLISH_EVCS_FIELDS_KIND, PUBLISH_COMPANION_FIELDS_KIND}


def _normalized_state(state: str) -> str:
    normalized = str(state).strip().lower()
    if normalized in {"ok", "congested", "protective"}:
        return normalized
    return "slow"


def _normalized_priority(command: CommandMapping) -> str:
    priority = _command_text(command, "priority").strip().lower()
    return priority or "diagnostic"


def _effective_queue_class(command: CommandMapping) -> str:
    configured = _command_text(command, "queue_class")
    return configured or command_queue_class(command)


def _is_startup_registration(queue_class: str) -> bool:
    return queue_class == "startup/register"
