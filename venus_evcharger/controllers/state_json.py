# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON payload contracts for runtime-state persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from venus_evcharger.controllers.state_contracts import string_object_mapping

_UTF8 = "utf-8"


def json_object_payload(value: object) -> dict[str, object] | None:
    """Return a JSON object payload with string keys, or None for invalid state."""
    return string_object_mapping(value)


def read_json_object_file(path: str) -> dict[str, object] | None:
    """Read a JSON object from disk, warning and returning None for invalid input."""
    try:
        loaded_state: object = json.loads(Path(path).read_text(encoding=_UTF8))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        logging.warning("Unable to read runtime state from %s: %s", path, error)
        return None
    payload = json_object_payload(loaded_state)
    if payload is None:
        logging.warning("Ignoring runtime state from %s: expected JSON object", path)
    return payload
