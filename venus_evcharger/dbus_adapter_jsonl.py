# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded JSONL helpers for RAM-backed DBus gateway diagnostics."""

from __future__ import annotations

import os
from collections.abc import Mapping

from venus_evcharger.core.shared import compact_json

DEFAULT_COMMAND_LIFECYCLE_MAX_BYTES = 1_048_576
DEFAULT_HEALTH_HISTORY_MAX_BYTES = 524_288


def append_jsonl(path: str, payload: Mapping[str, object], *, max_bytes: int) -> None:
    ensure_parent_dir(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(compact_json(payload) + "\n")
    retain_jsonl_tail(path, max_bytes=max_bytes)


def ensure_parent_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def retain_jsonl_tail(path: str, *, max_bytes: int) -> None:
    limit = int(max_bytes)
    if limit <= 0:
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size <= limit:
        return
    rewrite_jsonl_tail(path, target_bytes=trim_target_bytes(limit), size=size)


def trim_target_bytes(max_bytes: int) -> int:
    return max(1, int(max_bytes) * 3 // 4)


def rewrite_jsonl_tail(path: str, *, target_bytes: int, size: int) -> None:
    started_mid_file = size > target_bytes
    with open(path, "rb") as handle:
        if started_mid_file:
            handle.seek(-target_bytes, os.SEEK_END)
        data = handle.read()
    with open(path, "wb") as handle:
        handle.write(drop_partial_first_jsonl_line(data) if started_mid_file else data)


def drop_partial_first_jsonl_line(data: bytes) -> bytes:
    newline = data.find(b"\n")
    return data if newline < 0 else data[newline + 1 :]
