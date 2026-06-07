# SPDX-License-Identifier: GPL-3.0-or-later
"""Tasmota-style HTTP/JSON switch backends."""

from __future__ import annotations

from .template_switch import TemplateContactorSwitchMixin, TemplateSwitchBackend


class TasmotaSwitchBackend(TemplateSwitchBackend):
    """Template-backed switch alias for Tasmota HTTP/JSON devices."""


class TasmotaContactorSwitchBackend(TemplateContactorSwitchMixin, TasmotaSwitchBackend):
    """Tasmota switch backend treated as an external contactor by default."""
