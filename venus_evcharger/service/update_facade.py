# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit update-cycle boundary for the wallbox service."""

from __future__ import annotations

from .composition_contracts import ControllerOwnerPort


class ServiceUpdateFacade:
    """Expose only the update use cases required by the productive service."""

    def __init__(self, controllers: ControllerOwnerPort) -> None:
        self._controllers = controllers

    def update(self) -> bool:
        return self._controllers.runtime.update.update()

    def sign_of_life(self) -> bool:
        return self._controllers.runtime.update.sign_of_life()
