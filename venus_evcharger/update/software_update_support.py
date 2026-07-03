# SPDX-License-Identifier: GPL-3.0-or-later
"""Software-update role facade for the update cycle."""

from __future__ import annotations

from venus_evcharger.update.software_update_run import _SoftwareUpdateRun


class _UpdateCycleSoftwareUpdate(_SoftwareUpdateRun):
    """Expose software-update helpers through the historical role name."""
