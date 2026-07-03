# SPDX-License-Identifier: GPL-3.0-or-later
"""Expected software-update boundary errors."""

from __future__ import annotations

import subprocess


SOFTWARE_UPDATE_PROCESS_ERRORS = (
    OSError,
    RuntimeError,
    subprocess.SubprocessError,
    ValueError,
)

SOFTWARE_UPDATE_CHECK_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
