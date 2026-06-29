# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared typing contracts for composable controller roles.

The wallbox controllers are assembled from focused role classes so production
files stay small and each role keeps one responsibility. Mypy cannot infer that
cross-file composition by itself, so this lightweight base class describes the
"assembled elsewhere" contract without adding runtime behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _ComposableControllerRole:
    """Type-checking contract for roles completed by a concrete controller."""

    if TYPE_CHECKING:  # pragma: no cover
        service: Any

        def __getattr__(self, name: str) -> Any: ...


ComposableControllerRole = _ComposableControllerRole
