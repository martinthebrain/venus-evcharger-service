# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive prompt primitives for the setup wizard CLI."""

from __future__ import annotations

import getpass


def _prompt_text(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def _prompt_password(default: str) -> str:
    if default and prompt_yes_no("Reuse imported password?", True):
        return default
    return getpass.getpass("Password: ")


def prompt_yes_no(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes", "1", "true", "on")


def _choice_from_raw(raw: str, choices: tuple[str, ...]) -> str | None:
    if raw.isdigit():
        numeric = int(raw)
        if 1 <= numeric <= len(choices):
            return choices[numeric - 1]
    return raw if raw in choices else None


def _prompt_choice_input(default: str | None) -> str:
    return input(f"Select [{default or 1}]: ").strip()


def _prompt_choice(
    prompt: str,
    choices: tuple[str, ...],
    labels: dict[str, str] | None = None,
    default: str | None = None,
) -> str:
    print(prompt)
    for index, choice in enumerate(choices, start=1):
        label = labels.get(choice, choice) if labels is not None else choice
        print(f"  {index}. {label}")
    while True:
        resolved = _resolved_choice_input(_prompt_choice_input(default), choices, default)
        if resolved is not None:
            return resolved
        print("Invalid selection, please try again.")


def _resolved_choice_input(raw: str, choices: tuple[str, ...], default: str | None) -> str | None:
    if not raw and default is not None:
        return default
    return _choice_from_raw(raw, choices)


def _prompt_optional_choice(
    prompt: str,
    choices: tuple[str, ...],
    labels: dict[str, str],
    default: str | None,
) -> str | None:
    selected = _prompt_choice(prompt, choices, labels, default or "none")
    return None if selected == "none" else selected
