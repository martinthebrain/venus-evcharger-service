# SPDX-License-Identifier: GPL-3.0-or-later
"""Tasmota-style HTTP/JSON switch backends."""

from __future__ import annotations

from .template_switch import TemplateSwitchBackend, force_contactor_switch_settings


class TasmotaSwitchBackend(TemplateSwitchBackend):
    """Template-backed switch alias for Tasmota HTTP/JSON devices."""


class TasmotaContactorSwitchBackend(TasmotaSwitchBackend):
    """Tasmota switch backend treated as an external contactor by default."""

    def __init__(self, service: object, config_path: str = "") -> None:
        super().__init__(service, config_path=config_path)
        force_contactor_switch_settings(self)
