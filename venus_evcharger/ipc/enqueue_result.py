# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed acceptance result for gateway IPC commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GatewayEnqueueTransport = Literal["none", "socket", "mailbox"]
GatewayEnqueueFailure = Literal[
    "",
    "backpressure",
    "invalid-command",
    "mailbox-lock-timeout",
]


@dataclass(frozen=True, slots=True)
class GatewayEnqueueResult:
    """Describe whether and how one gateway command was accepted."""

    accepted: bool
    command_id: str = ""
    transport: GatewayEnqueueTransport = "none"
    reason: GatewayEnqueueFailure = ""
    command_path: str = ""


__all__ = [
    "GatewayEnqueueFailure",
    "GatewayEnqueueResult",
    "GatewayEnqueueTransport",
]
