# SPDX-License-Identifier: GPL-3.0-or-later
"""Live-connectivity checks for rendered wizard setups."""

from __future__ import annotations

import configparser
import tempfile
from pathlib import Path
from typing import Callable

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.probe import probe_meter_backend, probe_switch_backend, read_charger_backend
from venus_evcharger.bootstrap.errors import WIZARD_ROLE_PROBE_ERRORS
from venus_evcharger.bootstrap.wizard_render import (
    materialize_rendered_setup,
    redact_sensitive_rendered_setup,
    sensitive_defaults_from_config_text,
)
from venus_evcharger.bootstrap.wizard_render_secrets import probe_service_from_wallbox_config
from venus_evcharger.bootstrap.wizard_runtime_results import json_ready


CONFIG_ENCODING = "utf-8"


def combined_role_payload(role: str, backend: object, main_path: Path, backend_type: object) -> dict[str, object]:
    if role == "meter":
        return {"path": str(main_path), "type": str(backend_type), "meter": json_ready(getattr(backend, "read_meter")())}
    if role == "switch":
        return {
            "path": str(main_path),
            "type": str(backend_type),
            "capabilities": json_ready(getattr(backend, "capabilities")()),
            "switch_state": json_ready(getattr(backend, "read_switch_state")()),
        }
    return {
        "path": str(main_path),
        "type": str(backend_type),
        "charger_state": json_ready(getattr(backend, "read_charger_state")()),
    }


def json_ready_dict(value: object, label: str) -> dict[str, object]:
    """Return one JSON-ready dict or fail with a focused runtime error."""
    ready = json_ready(value)
    if isinstance(ready, dict):
        return {str(key): item for key, item in ready.items()}
    raise TypeError(f"{label} did not render to a JSON object")


def live_connectivity_payload(
    main_path: Path,
    selected_roles: tuple[str, ...] | None,
    secret_defaults: dict[str, str] | None = None,
) -> dict[str, object]:
    return live_connectivity_payload_with_hooks(
        main_path,
        selected_roles,
        secret_defaults=secret_defaults,
        build_backends_fn=build_service_backends,
        probe_meter_fn=probe_meter_backend,
        probe_switch_fn=probe_switch_backend,
        read_charger_fn=read_charger_backend,
    )


def live_connectivity_payload_with_hooks(
    main_path: Path,
    selected_roles: tuple[str, ...] | None,
    *,
    build_backends_fn: Callable[[object], object],
    probe_meter_fn: Callable[[str], dict[str, object]],
    probe_switch_fn: Callable[[str], dict[str, object]],
    read_charger_fn: Callable[[str], dict[str, object]],
    secret_defaults: dict[str, str] | None = None,
) -> dict[str, object]:
    parser = configparser.ConfigParser()
    parser.read(main_path, encoding=CONFIG_ENCODING)
    role_results: dict[str, dict[str, object]] = {}
    ok = True
    resolved_backends = build_backends_fn(probe_service_from_wallbox_config(parser, secret_defaults))
    runtime = getattr(resolved_backends, "runtime")

    def backend_for(role: str) -> object | None:
        return getattr(resolved_backends, role, None)

    def run_role(
        role: str,
        backend_type: object,
        config_path: Path | None,
        probe: Callable[[str], dict[str, object]],
    ) -> None:
        nonlocal ok
        if selected_roles is not None and role not in selected_roles:
            role_results[role] = {"status": "skipped", "reason": "not requested"}
            return
        if getattr(runtime, "backend_mode", None) == "combined" and config_path is None and role in {"meter", "switch"}:
            backend = backend_for(role)
            if backend is None:
                role_results[role] = {"status": "skipped", "reason": "not configured"}
                return
            try:
                role_results[role] = {
                    "status": "ok",
                    "payload": combined_role_payload(role, backend, main_path, backend_type),
                }
            except WIZARD_ROLE_PROBE_ERRORS as exc:
                ok = False
                role_results[role] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            return
        if config_path is None:
            role_results[role] = {"status": "skipped", "reason": "not configured"}
            return
        resolved_path = config_path if config_path.is_absolute() else main_path.parent / config_path
        try:
            if secret_defaults is not None:
                backend = backend_for(role)
                if backend is None:
                    role_results[role] = {"status": "skipped", "reason": "not configured"}
                    return
                role_results[role] = {
                    "status": "ok",
                    "payload": combined_role_payload(role, backend, resolved_path, backend_type),
                }
                return
            role_results[role] = {"status": "ok", "payload": probe(str(resolved_path))}
        except WIZARD_ROLE_PROBE_ERRORS as exc:
            ok = False
            role_results[role] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    run_role("meter", getattr(runtime, "meter_type", "shelly_meter"), runtime.meter_config_path, probe_meter_fn)
    run_role("switch", getattr(runtime, "switch_type", "shelly_contactor_switch"), runtime.switch_config_path, probe_switch_fn)
    run_role("charger", getattr(runtime, "charger_type", ""), runtime.charger_config_path, read_charger_fn)
    checked_roles = tuple(role for role, payload in role_results.items() if payload.get("status") != "skipped")
    return {"ok": ok, "checked_roles": checked_roles, "roles": role_results}


def live_check_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
    config_name: str,
    selected_roles: tuple[str, ...] | None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        redacted_config_text, redacted_adapter_files = redact_sensitive_rendered_setup(config_text, adapter_files)
        main_path = materialize_rendered_setup(redacted_config_text, temp_path, redacted_adapter_files, config_name)
        return live_connectivity_payload(main_path, selected_roles, sensitive_defaults_from_config_text(config_text))
