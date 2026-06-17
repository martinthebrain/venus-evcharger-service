# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch-friendly main-module facade."""

from venus_evcharger_service import ShellyWallboxService, main

__all__ = ("ShellyWallboxService", "main")
