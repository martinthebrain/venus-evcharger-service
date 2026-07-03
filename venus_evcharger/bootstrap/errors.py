# SPDX-License-Identifier: GPL-3.0-or-later
"""Expected bootstrap and wizard boundary errors."""

from __future__ import annotations

import configparser


BOOTSTRAP_DBUS_REGISTRATION_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)

BOOTSTRAP_DEVICE_INFO_ERRORS = (*BOOTSTRAP_DBUS_REGISTRATION_ERRORS, AttributeError)

WIZARD_ROLE_PROBE_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    configparser.Error,
)
