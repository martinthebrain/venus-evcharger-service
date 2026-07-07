# SPDX-License-Identifier: GPL-3.0-or-later
"""Endpoint helpers for wizard-generated inventory devices."""

from __future__ import annotations

from urllib.parse import urlparse


def _phase_endpoint(switch_host_input: str, phase_label: str) -> str:
    parsed = urlparse(switch_host_input)
    if parsed.scheme:
        base = switch_host_input.rstrip("/")
    else:
        base = f"http://{switch_host_input.rstrip('/')}"
    suffix = {"L1": "/wizard/phase1", "L2": "/wizard/phase2", "L3": "/wizard/phase3"}[phase_label]
    return f"{base}{suffix}"
