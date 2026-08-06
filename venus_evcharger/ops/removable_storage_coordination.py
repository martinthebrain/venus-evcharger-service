# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinate removable-storage writes with an external maintenance service.

The EV charger never repairs or unmounts removable storage. Writers acquire a
shared lease, while the independent maintenance service acquires an exclusive
lease before unmounting, checking, or repairing a device.
"""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from collections.abc import Generator


DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH = (
    "/run/lock/removable-storage-maintenance.lock"
)


@contextmanager
def removable_storage_write_lease(
    lock_path: str = DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH,
) -> Generator[bool, None, None]:
    """Yield whether a non-blocking shared removable-storage lease was acquired."""
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, mode=0o755, exist_ok=True)
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
        0o600,
    )
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


__all__ = [
    "DEFAULT_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH",
    "removable_storage_write_lease",
]
