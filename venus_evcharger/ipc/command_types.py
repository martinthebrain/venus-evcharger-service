# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral command payload types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

CommandMapping: TypeAlias = Mapping[str, object]
CommandPayload: TypeAlias = dict[str, object]
CommandFile: TypeAlias = tuple[str, CommandMapping]
CommandFileList: TypeAlias = list[CommandFile]
CommandOrderKey: TypeAlias = tuple[int, int, float, int, str]
