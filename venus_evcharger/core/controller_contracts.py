# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared typing contracts for controller fragments assembled elsewhere.

The wallbox controllers are assembled from focused fragments so production files
stay small and each fragment keeps one responsibility. Mypy cannot infer that
cross-file composition by itself, so this lightweight base class describes the
"assembled elsewhere" contract without adding runtime behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class ControllerAssemblyContract:
    """Type-checking contract for fragments completed by a concrete controller."""

    if TYPE_CHECKING:  # pragma: no cover
        service: Any

        def __getattr__(self, name: str) -> Any: ...
