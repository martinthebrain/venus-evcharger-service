# SPDX-License-Identifier: GPL-3.0-or-later
"""Tuya-style HTTP/JSON switch backends."""

from __future__ import annotations

from .template_switch import TemplateContactorSwitchMixin, TemplateSwitchBackend


class TuyaSwitchBackend(TemplateSwitchBackend):
    """Template-backed switch alias for Tuya-compatible local HTTP bridges."""


class TuyaContactorSwitchBackend(TemplateContactorSwitchMixin, TuyaSwitchBackend):
    """Tuya switch backend treated as an external contactor by default."""
