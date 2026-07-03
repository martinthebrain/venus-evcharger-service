# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus gateway command payload types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

CommandMapping: TypeAlias = Mapping[str, object]
CommandPayload: TypeAlias = dict[str, object]
CommandFile: TypeAlias = tuple[str, CommandPayload]
CommandFileList: TypeAlias = list[CommandFile]
