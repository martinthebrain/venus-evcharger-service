# SPDX-License-Identifier: GPL-3.0-or-later
"""Type aliases for async runtime queues."""

from __future__ import annotations

from typing import Any

from venus_evcharger.control import ControlCommand

QueuedPublishValue = tuple[Any, float, float]
QueuedControlCommand = tuple[int, float, ControlCommand]
