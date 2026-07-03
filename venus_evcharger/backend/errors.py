# SPDX-License-Identifier: GPL-3.0-or-later
"""Expected backend I/O boundary errors."""

from __future__ import annotations


BACKEND_IO_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)

BACKEND_OPTIONAL_CAPABILITY_ERRORS = (*BACKEND_IO_ERRORS, AttributeError)
