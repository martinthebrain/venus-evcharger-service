# SPDX-License-Identifier: GPL-3.0-or-later
"""Software-update mixin facade for the update cycle."""

from __future__ import annotations

from venus_evcharger.update.software_update_run import _SoftwareUpdateRunMixin


class _UpdateCycleSoftwareUpdateMixin(_SoftwareUpdateRunMixin):
    """Expose software-update helpers through the historical mixin name."""
