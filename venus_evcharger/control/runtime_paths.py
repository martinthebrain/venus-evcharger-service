# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime path checks shared by Control API helpers."""

from __future__ import annotations

import os


def is_runtime_path(path: str) -> bool:
    """Return whether a path points at volatile runtime storage."""
    normalized = os.path.abspath(path)
    # These are the two explicit volatile roots accepted by the local control transport.
    return normalized.startswith("/run/") or normalized.startswith("/tmp/")  # nosec B108
