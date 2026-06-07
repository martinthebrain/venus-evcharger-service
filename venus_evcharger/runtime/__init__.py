# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime helpers exposed under ``venus_evcharger.runtime``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .audit import _RuntimeSupportAuditMixin
from .async_mainloop import _RuntimeSupportAsyncMainloopMixin
from .health import _RuntimeSupportHealthMixin
from .setup import _RuntimeSupportSetupMixin

if TYPE_CHECKING:
    from .support import RuntimeSupportController

__all__ = [
    "RuntimeSupportController",
    "_RuntimeSupportAuditMixin",
    "_RuntimeSupportAsyncMainloopMixin",
    "_RuntimeSupportHealthMixin",
    "_RuntimeSupportSetupMixin",
]


def __getattr__(name: str) -> Any:
    if name == "RuntimeSupportController":
        from .support import RuntimeSupportController

        return RuntimeSupportController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
