# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime helpers exposed under ``venus_evcharger.runtime``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .audit import _RuntimeAudit
from .async_mainloop import _RuntimeAsyncMainloop
from .health import _RuntimeHealth
from .setup import _RuntimeSetup

if TYPE_CHECKING:
    from .support import RuntimeSupportController

__all__ = [
    "RuntimeSupportController",
    "_RuntimeAudit",
    "_RuntimeAsyncMainloop",
    "_RuntimeHealth",
    "_RuntimeSetup",
]


def __getattr__(name: str) -> Any:
    if name == "RuntimeSupportController":
        from .support import RuntimeSupportController

        return RuntimeSupportController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
