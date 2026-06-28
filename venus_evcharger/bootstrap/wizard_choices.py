# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed choice normalization shared by wizard input paths."""

from __future__ import annotations

from typing import TypeVar

_ChoiceT = TypeVar("_ChoiceT", bound=str)


def optional_choice(raw_value: object, allowed: tuple[_ChoiceT, ...], label: str) -> _ChoiceT | None:
    """Return one typed choice value or raise when the input is unsupported."""
    if raw_value is None:
        return None
    text = str(raw_value)
    known = recognized_choice(text, allowed)
    if known is not None:
        return known
    raise ValueError(f"Unsupported {label}: {text}")


def recognized_choice(raw_value: object, allowed: tuple[_ChoiceT, ...]) -> _ChoiceT | None:
    """Return one typed choice value, or None when the value is not recognized."""
    text = str(raw_value)
    for value in allowed:
        if text == value:
            return value
    return None
