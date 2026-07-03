# SPDX-License-Identifier: GPL-3.0-or-later
"""Expected controller boundary errors."""

from __future__ import annotations

import configparser


CONTROL_COMMAND_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)

RUNTIME_OVERRIDE_READ_ERRORS = (
    OSError,
    RuntimeError,
    UnicodeDecodeError,
    configparser.Error,
)

RUNTIME_PERSISTENCE_WRITE_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    UnicodeEncodeError,
    ValueError,
)

WRITE_SNAPSHOT_DBUS_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)
