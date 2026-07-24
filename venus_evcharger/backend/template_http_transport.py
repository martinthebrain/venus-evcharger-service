# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP session and method contracts for template-backed adapters."""

from __future__ import annotations

from collections.abc import Callable
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


@runtime_checkable
class HttpGetSession(Protocol):
    def get(self, **kwargs: object) -> HttpResponse: ...


@runtime_checkable
class HttpPostSession(Protocol):
    def post(self, **kwargs: object) -> HttpResponse: ...


@runtime_checkable
class HttpPutSession(Protocol):
    def put(self, **kwargs: object) -> HttpResponse: ...


@runtime_checkable
class HttpPatchSession(Protocol):
    def patch(self, **kwargs: object) -> HttpResponse: ...


@runtime_checkable
class DynamicHttpSession(Protocol):
    def __getattr__(self, name: str) -> object: ...


def http_session(value: object | None) -> object:
    """Return an injected session or one reusable requests session."""
    return requests.Session() if value is None else value


def request_method_callable(session: object, method: str) -> HttpRequestCallable:
    """Return the bound session method for one supported HTTP verb."""
    normalized_method = str(method).strip().upper()
    resolver = _HTTP_METHOD_RESOLVERS.get(normalized_method)
    if resolver is None:
        raise ValueError(f"Unsupported template backend HTTP method '{method}'")
    return resolver(session)


def _get_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpGetSession):
        return session.get
    return _dynamic_request_callable(session, "get", "GET")


def _post_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpPostSession):
        return session.post
    return _dynamic_request_callable(session, "post", "POST")


def _put_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpPutSession):
        return session.put
    return _dynamic_request_callable(session, "put", "PUT")


def _patch_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpPatchSession):
        return session.patch
    return _dynamic_request_callable(session, "patch", "PATCH")


def _dynamic_request_callable(
    session: object,
    member_name: str,
    method: str,
) -> HttpRequestCallable:
    if not isinstance(session, DynamicHttpSession):
        raise TypeError(f"Template backend session does not implement HTTP {method}")
    candidate = session.__getattr__(member_name)
    if not _http_request_callable(candidate):
        raise TypeError(f"Template backend session does not implement HTTP {method}")
    return candidate


def _http_request_callable(value: object) -> TypeGuard[HttpRequestCallable]:
    return callable(value)


_HTTP_METHOD_RESOLVERS: dict[str, Callable[[object], HttpRequestCallable]] = {
    "GET": _get_request_callable,
    "POST": _post_request_callable,
    "PUT": _put_request_callable,
    "PATCH": _patch_request_callable,
}


__all__ = [
    "HttpRequestCallable",
    "HttpResponse",
    "http_session",
    "request_method_callable",
]
