# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for runtime-generated output files."""

from __future__ import annotations

import os
from pathlib import Path


def _path_error(label: str, suffix: str) -> ValueError:
    return ValueError(f"{label} must be an absolute {suffix} file path")


def _normalized_path_text(value: object, *, label: str, suffix: str) -> str:
    if not isinstance(value, (str, os.PathLike)):
        raise _path_error(label, suffix)
    raw_path = os.fspath(value)
    if not isinstance(raw_path, str):
        raise _path_error(label, suffix)
    normalized = raw_path.strip()
    if "\x00" in normalized:
        raise _path_error(label, suffix)
    return normalized


def _is_expected_output_file(path: Path, suffix: str) -> bool:
    return path.is_absolute() and path.suffix.lower() == suffix.lower()


def validated_output_file_path(value: object, *, label: str, suffix: str) -> str:
    """Return one explicit absolute output file path or fail fast."""

    path = Path(_normalized_path_text(value, label=label, suffix=suffix))
    if not _is_expected_output_file(path, suffix):
        raise _path_error(label, suffix)
    return str(path)
