# SPDX-License-Identifier: GPL-3.0-or-later
"""Ownership of one reusable HTTP session for external connectors."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import requests


@runtime_checkable
class ClosableHttpSession(Protocol):
    """Session lifecycle needed by the helper process."""

    def close(self) -> None: ...


class ConnectorHttpSession:
    """Create, share, and conditionally close one connector HTTP session."""

    def __init__(self, enabled: bool, injected: object | None) -> None:
        self._owned = enabled and injected is None
        self.session: object | None = (
            requests.Session() if self._owned else injected
        )

    def close(self) -> None:
        if not self._owned:
            return
        session = self.session
        if not isinstance(session, ClosableHttpSession):
            raise TypeError("Owned connector HTTP session cannot be closed")
        session.close()
        self.session = None
        self._owned = False


__all__ = ["ClosableHttpSession", "ConnectorHttpSession"]
