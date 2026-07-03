# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway queue classes and backpressure policy."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.dbus_gateway_command_types import CommandMapping
from venus_evcharger.dbus_gateway_core import FAST_READ_KEYS, GUI_CRITICAL_PUBLISH_PATHS
from venus_evcharger.dbus_gateway_surface import evcs_fields_to_paths

_STATIC_QUEUE_CLASSES = {
    "register_path": "startup/register",
    "register_service": "startup/register",
    "refresh_value": "read-slow",
    "set_value": "remote-write",
    "refresh_services": "discovery",
    "introspect": "introspection",
}


def _command_text(command: CommandMapping, key: str) -> str:
    if key not in command:
        return ""
    return str(command[key] or "")


def command_queue_class(command: CommandMapping) -> str:
    kind = _command_kind(command)
    if _is_publish_command(kind):
        return _publish_queue_class(command)
    if kind == "refresh_value":
        return _refresh_queue_class(command)
    return _STATIC_QUEUE_CLASSES.get(kind, "diagnostic")


def _publish_queue_class(command: CommandMapping) -> str:
    return "gui-critical-publish" if _is_gui_critical_publish(command) else "local-publish"


def _refresh_queue_class(command: CommandMapping) -> str:
    return "read-fast" if _refresh_key(command) in FAST_READ_KEYS else "read-slow"


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
    return priority == "safety" or (priority == "user" and queue_class == "gui-critical-publish")


def _is_gui_critical_publish(command: CommandMapping) -> bool:
    return (
        _single_publish_path_is_gui_critical(command)
        or _publish_paths_are_gui_critical(command)
        or _publish_fields_are_gui_critical(command)
    )


def _single_publish_path_is_gui_critical(command: CommandMapping) -> bool:
    return _publish_path(command) in GUI_CRITICAL_PUBLISH_PATHS


def _publish_paths_are_gui_critical(command: CommandMapping) -> bool:
    paths = command.get("paths")
    return isinstance(paths, Mapping) and any(str(item) in GUI_CRITICAL_PUBLISH_PATHS for item in paths)


def _publish_fields_are_gui_critical(command: CommandMapping) -> bool:
    fields = command.get("fields")
    if not isinstance(fields, Mapping):
        return False
    mapped_paths = evcs_fields_to_paths({str(field): value for field, value in fields.items()})
    return any(path in GUI_CRITICAL_PUBLISH_PATHS for path in mapped_paths)


def _command_kind(command: CommandMapping) -> str:
    return _command_text(command, "kind") or _command_text(command, "type")


def _is_publish_command(kind: str) -> bool:
    return kind in {"publish_value", "publish_desired", "publish_fields"}


def _refresh_key(command: CommandMapping) -> str:
    return _command_text(command, "key")


def _normalized_state(state: str) -> str:
    return str(state).strip().lower()


def _normalized_priority(command: CommandMapping) -> str:
    priority = _command_text(command, "priority").strip().lower()
    return priority or "diagnostic"


def _effective_queue_class(command: CommandMapping) -> str:
    configured = _command_text(command, "queue_class")
    return configured or command_queue_class(command)


def _is_startup_registration(queue_class: str) -> bool:
    return queue_class == "startup/register"


def _publish_path(command: CommandMapping) -> str:
    return _command_text(command, "path")
