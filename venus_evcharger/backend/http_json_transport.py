# SPDX-License-Identifier: GPL-3.0-or-later
"""Size-bounded JSON response decoding for HTTP transports."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class StreamingHttpResponse(Protocol):
    """Streaming response surface required for bounded decoding."""

    @property
    def headers(self) -> Mapping[str, str]: ...

    def iter_content(self, chunk_size: int) -> Iterable[bytes]: ...

    def close(self) -> None: ...


def decoded_json_response(response: object, max_bytes: int | None) -> object:
    """Decode JSON while enforcing the limit before response allocation."""
    if max_bytes is None:
        return _decoded_unbounded_response(response)
    return _decoded_bounded_response(response, max_bytes)


def _decoded_unbounded_response(response: object) -> object:
    json_method = getattr(response, "json", None)
    if not callable(json_method):
        raise TypeError("HTTP response does not provide JSON decoding")
    return json_method()


def _decoded_bounded_response(response: object, max_bytes: int) -> object:
    if max_bytes < 1:
        raise ValueError("HTTP JSON response limit must be positive")
    if not isinstance(response, StreamingHttpResponse):
        raise TypeError("Bounded HTTP JSON response does not support streaming")
    try:
        _reject_declared_oversize(response.headers, max_bytes)
        payload = _read_bounded_content(response.iter_content(16384), max_bytes)
    finally:
        response.close()
    return _decode_payload(payload)


def _decode_payload(payload: bytes) -> object:
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


def _reject_declared_oversize(headers: Mapping[str, str], max_bytes: int) -> None:
    raw_length = headers.get("Content-Length")
    if raw_length is None:
        return
    try:
        content_length = int(raw_length)
    except (TypeError, ValueError) as error:
        raise ValueError("HTTP response has an invalid Content-Length") from error
    if content_length < 0:
        raise ValueError("HTTP response has a negative Content-Length")
    if content_length > max_bytes:
        raise ValueError(f"HTTP JSON response exceeds {max_bytes} bytes")


def _read_bounded_content(chunks: Iterable[bytes], max_bytes: int) -> bytes:
    payload = bytearray()
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise TypeError("HTTP response stream yielded non-bytes content")
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise ValueError(f"HTTP JSON response exceeds {max_bytes} bytes")
    return bytes(payload)


__all__ = ["StreamingHttpResponse", "decoded_json_response"]
