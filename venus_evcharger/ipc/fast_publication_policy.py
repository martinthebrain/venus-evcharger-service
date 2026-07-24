# SPDX-License-Identifier: GPL-3.0-or-later
"""Admission policy and stable identifiers for transient publications."""

from __future__ import annotations

import hashlib

from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_publication import (
    PUBLISH_COMPANION_FIELDS_KIND,
    PUBLISH_EVCS_FIELDS_KIND,
    parse_publish_companion_fields,
    parse_publish_evcs_fields,
)

_TRANSIENT_PRIORITIES = frozenset(("live", "diagnostic"))


def is_transient_publication(command: CommandMapping) -> bool:
    """Return whether a command may safely disappear with the gateway process."""
    priority = str(command.get("publication_priority") or "")
    if priority not in _TRANSIENT_PRIORITIES:
        return False
    return _valid_transient_kind(command)


def fast_command_id(key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"fast-{digest}"


def _valid_transient_kind(command: CommandMapping) -> bool:
    kind = str(command.get("kind") or "")
    if kind == PUBLISH_EVCS_FIELDS_KIND:
        return parse_publish_evcs_fields(command) is not None
    if kind == PUBLISH_COMPANION_FIELDS_KIND:
        return parse_publish_companion_fields(command) is not None
    return False


__all__ = ["fast_command_id", "is_transient_publication"]
