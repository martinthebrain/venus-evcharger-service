# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON payload contracts for runtime-state persistence."""

from __future__ import annotations

import json
import logging


def json_object_payload(value: object) -> dict[str, object] | None:
    """Return a JSON object payload with string keys, or None for invalid state."""
    if not isinstance(value, dict):
        return None
    payload: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        payload[key] = item
    return payload


def read_json_object_file(path: str) -> dict[str, object] | None:
    """Read a JSON object from disk, warning and returning None for invalid input."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded_state: object = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as error:  # pylint: disable=broad-except
        logging.warning("Unable to read runtime state from %s: %s", path, error)
        return None
    payload = json_object_payload(loaded_state)
    if payload is None:
        logging.warning("Ignoring runtime state from %s: expected JSON object", path)
    return payload
