# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenAPI 3.1 description for the local Control and State API."""

from __future__ import annotations

from typing import Any

from .openapi_paths import _paths_spec
from .openapi_schemas import _component_schemas


def build_control_api_openapi_spec() -> dict[str, Any]:
    """Return the stable OpenAPI 3.1 description for the local HTTP API."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Venus EV Charger Service Local API",
            "version": "v1",
            "description": "Local command, state, and event API for the Venus EV charger service.",
        },
        "servers": [
            {"url": "http://127.0.0.1:8765"},
            {"url": "http+unix://localhost"},
        ],
        "paths": _paths_spec(),
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "opaque-token",
                }
            },
            "schemas": _component_schemas(),
        },
    }
