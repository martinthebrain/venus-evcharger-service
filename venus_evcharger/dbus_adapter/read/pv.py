#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic AC and DC PV source selection for adapter reads."""

from __future__ import annotations

from collections.abc import Sequence

from venus_evcharger.dbus_adapter.read.spec import ReadSpec

PV_MEMBER_ERROR_BACKOFF_SECONDS = 300.0


def pv_total_members(
    spec: ReadSpec,
    ac_services: Sequence[str],
) -> list[tuple[str, str]]:
    return [
        *ac_pv_members(spec, ac_services),
        *dc_pv_members(spec),
    ]


def ac_pv_members(
    spec: ReadSpec,
    services: Sequence[str],
) -> list[tuple[str, str]]:
    path = str(spec.get("path") or "")
    return [
        (service, path)
        for service in services
        if path
    ]


def dc_pv_members(
    spec: ReadSpec,
) -> list[tuple[str, str]]:
    target = dc_pv_target(spec) if use_dc_pv(spec) else None
    return [] if target is None else [target]


def dc_pv_target(spec: ReadSpec) -> tuple[str, str] | None:
    if "dc_service" not in spec or "dc_path" not in spec:
        return None
    service = _stripped_text(spec["dc_service"])
    path = _stripped_text(spec["dc_path"])
    if not service or not path.startswith("/"):
        return None
    return service, path


def use_dc_pv(spec: ReadSpec) -> bool:
    return spec.get("use_dc_pv") is True


def _stripped_text(raw_value: object) -> str:
    return raw_value.strip() if isinstance(raw_value, str) else ""
