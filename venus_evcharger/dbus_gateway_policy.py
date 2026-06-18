# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway queue classes and backpressure policy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.dbus_gateway_core import FAST_READ_KEYS, GUI_CRITICAL_PUBLISH_PATHS, _priority_rank

_STATIC_QUEUE_CLASSES = {
    "register_path": "startup/register",
    "register_service": "startup/register",
    "refresh_value": "read-slow",
    "set_value": "remote-write",
    "refresh_services": "discovery",
    "introspect": "introspection",
}


def command_queue_class(command: Mapping[str, Any]) -> str:
    kind = _command_kind(command)
    if _is_publish_command(kind):
        return _publish_queue_class(command)
    if kind == "refresh_value":
        return _refresh_queue_class(command)
    return _STATIC_QUEUE_CLASSES.get(kind, "diagnostic")


def _publish_queue_class(command: Mapping[str, Any]) -> str:
    return "gui-critical-publish" if _is_gui_critical_publish(command) else "local-publish"


def _refresh_queue_class(command: Mapping[str, Any]) -> str:
    return "read-fast" if _refresh_key(command) in FAST_READ_KEYS else "read-slow"


def command_allowed_by_backpressure(command: Mapping[str, Any], state: str) -> bool:
    normalized_state = _normalized_state(state)
    priority = _normalized_priority(command)
    queue_class = _effective_queue_class(command)
    return _backpressure_rule(normalized_state, priority, queue_class)


def _backpressure_rule(state: str, priority: str, queue_class: str) -> bool:
    if _state_allows_everything(state) or _is_startup_registration(queue_class):  # pragma: no mutate
        return True
    rules = {
        "congested": _allowed_when_congested,
        "slow": _allowed_when_slow,
        "protective": _allowed_when_protective,
    }
    return rules.get(state, _allow_any)(_priority_rank(priority), priority, queue_class)  # pragma: no mutate


def _allow_any(_rank: int, _priority: str, _queue_class: str) -> bool:
    return True


def _allowed_when_congested(_rank: int, priority: str, queue_class: str) -> bool:
    return priority not in {"optional", "diagnostic"} and queue_class != "diagnostic"


def _allowed_when_slow(_rank: int, priority: str, queue_class: str) -> bool:
    return queue_class == "gui-critical-publish" or priority in {"safety", "user"}


def _allowed_when_protective(_rank: int, priority: str, queue_class: str) -> bool:
    return priority == "safety" or (priority == "user" and queue_class == "gui-critical-publish")


def _is_gui_critical_publish(command: Mapping[str, Any]) -> bool:
    if _publish_path(command) in GUI_CRITICAL_PUBLISH_PATHS:
        return True
    paths = command.get("paths")
    if isinstance(paths, Mapping):
        return any(str(item) in GUI_CRITICAL_PUBLISH_PATHS for item in paths)
    return False


def _command_kind(command: Mapping[str, Any]) -> str:
    return str(command.get("kind") or command.get("type") or "")  # pragma: no mutate


def _is_publish_command(kind: str) -> bool:
    return kind in {"publish_value", "publish_desired"}


def _refresh_key(command: Mapping[str, Any]) -> str:
    return str(command.get("key") or "")  # pragma: no mutate


def _normalized_state(state: str) -> str:
    return str(state or "unknown")  # pragma: no mutate


def _normalized_priority(command: Mapping[str, Any]) -> str:
    return str(command.get("priority") or "diagnostic").strip().lower()  # pragma: no mutate


def _effective_queue_class(command: Mapping[str, Any]) -> str:
    return str(command.get("queue_class") or command_queue_class(command))  # pragma: no mutate


def _state_allows_everything(state: str) -> bool:
    return state in {"ok", "unknown"}  # pragma: no mutate


def _is_startup_registration(queue_class: str) -> bool:
    return queue_class == "startup/register"


def _publish_path(command: Mapping[str, Any]) -> str:
    return str(command.get("path") or "")  # pragma: no mutate
