#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared probes for DBus adapter socket contract tests."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import cast

from venus_evcharger.dbus_adapter.process import socket as socket_module
from venus_evcharger.dbus_gateway import GatewayPaths
from venus_evcharger.ipc.fast_publication import FastPublicationQueue


class EventLoopProbe:
    PRIORITY_DEFAULT: int = 17
    IO_IN: int = 23

    def __init__(self) -> None:
        self.watch_calls: list[tuple[int, int, int, Callable[[int, int], bool]]] = []
        self.timer_calls: list[tuple[int, Callable[[], bool]]] = []
        self.removed: list[int] = []
        self.next_source_id = 100

    def io_add_watch(
        self,
        source: int,
        priority: int,
        condition: int,
        callback: Callable[[int, int], bool],
    ) -> int:
        self.watch_calls.append((source, priority, condition, callback))
        self.next_source_id += 1
        return self.next_source_id

    def timeout_add(self, interval: int, callback: Callable[[], bool]) -> int:
        self.timer_calls.append((interval, callback))
        self.next_source_id += 1
        return self.next_source_id

    def source_remove(self, source_id: int) -> bool:
        self.removed.append(source_id)
        return True


class ConnectionProbe:
    def __init__(self, *reads: bytes | BaseException) -> None:
        self._reads = list(reads)
        self.recv_sizes: list[int] = []
        self.blocking: list[bool] = []
        self.sent: list[bytes] = []
        self.close_count = 0

    def recv(self, size: int) -> bytes:
        self.recv_sizes.append(size)
        if not self._reads:
            raise AssertionError("unexpected socket read")
        result = self._reads.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def setblocking(self, blocking: bool) -> None:
        self.blocking.append(blocking)

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.close_count += 1


class ServerProbe:
    def __init__(
        self,
        connection: ConnectionProbe | None = None,
        *,
        accept_error: BaseException | None = None,
    ) -> None:
        self.connection = connection
        self.accept_error = accept_error
        self.accept_count = 0
        self.bind_calls: list[str] = []
        self.listen_calls: list[int] = []
        self.blocking: list[bool] = []
        self.close_count = 0

    def accept(self) -> tuple[socket.socket, object]:
        self.accept_count += 1
        if self.accept_error is not None:
            raise self.accept_error
        if self.connection is None:
            raise AssertionError("accept called without a connection")
        return cast(socket.socket, self.connection), object()

    def bind(self, path: str) -> None:
        self.bind_calls.append(path)

    def listen(self, backlog: int) -> None:
        self.listen_calls.append(backlog)

    def setblocking(self, blocking: bool) -> None:
        self.blocking.append(blocking)

    def fileno(self) -> int:
        return 41

    def close(self) -> None:
        self.close_count += 1


class SocketContext:
    def __init__(self, paths: GatewayPaths) -> None:
        self.paths = paths
        self._server: socket.socket | None = None
        self.fast_publications = FastPublicationQueue()


def pending_connection() -> socket_module._PendingSocketRequest:
    connection = cast(socket.socket, ConnectionProbe(BlockingIOError()))
    return socket_module._PendingSocketRequest(
        connection,
        10.0,
        bytearray(),
    )
