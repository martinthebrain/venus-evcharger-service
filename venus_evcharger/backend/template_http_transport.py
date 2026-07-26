# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP session and method contracts for template-backed adapters."""

from __future__ import annotations

from typing import Protocol, TypeGuard, runtime_checkable

import requests


@runtime_checkable
class HttpResponse(Protocol):
    """Minimal response contract required by HTTP-backed adapters."""

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class HttpRequestCallable(Protocol):
    """Callable contract shared by supported HTTP verbs."""

    def __call__(
        self,
        *,
        url: str,
        timeout: float,
        json: object = ...,
        params: dict[str, str] = ...,
        auth: object = ...,
        headers: dict[str, str] = ...,
        stream: bool = ...,
    ) -> HttpResponse: ...


def http_session(value: object | None) -> object:
    """Return an injected session or one reusable requests session."""
    return requests.Session() if value is None else value


def request_method_callable(session: object, method: str) -> HttpRequestCallable:
    """Return the bound session method for one supported HTTP verb."""
    normalized_method = str(method).strip().upper()
    if normalized_method not in _HTTP_METHODS:
        raise ValueError(f"Unsupported template backend HTTP method '{method}'")
    candidate = getattr(session, normalized_method.lower(), None)
    if not _http_request_callable(candidate):
        raise TypeError(f"Template backend session does not implement HTTP {normalized_method}")
    return candidate


def _http_request_callable(value: object) -> TypeGuard[HttpRequestCallable]:
    return callable(value)


_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH"})


__all__ = [
    "HttpRequestCallable",
    "HttpResponse",
    "http_session",
    "request_method_callable",
]
