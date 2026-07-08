# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
import tempfile
from pathlib import Path
from typing import Callable

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.probe import probe_meter_backend, probe_switch_backend, read_charger_backend
from venus_evcharger.bootstrap.errors import WIZARD_ROLE_PROBE_ERRORS
from venus_evcharger.bootstrap.wizard_render_io import materialize_rendered_setup
from venus_evcharger.bootstrap.wizard_render_secrets import (
    probe_service_from_wallbox_config,
    redact_sensitive_rendered_setup,
    sensitive_defaults_from_config_text,
)


CONFIG_ENCODING = "utf-8"


def _read_main_config(main_path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(main_path, encoding=CONFIG_ENCODING)
    return parser


def live_connectivity_payload(
    main_path: Path,
    selected_roles: tuple[str, ...] | None,
    secret_defaults: dict[str, str] | None = None,
) -> dict[str, object]:
    parser = _read_main_config(main_path)
    runtime = build_service_backends(probe_service_from_wallbox_config(parser, secret_defaults)).runtime
    role_results: dict[str, dict[str, object]] = {}
    ok = True

    def run_role(
        role: str,
        config_path: Path | None,
        probe: Callable[[str], dict[str, object]],
    ) -> None:
        nonlocal ok
        if selected_roles is not None and role not in selected_roles:
            role_results[role] = {"status": "skipped", "reason": "not requested"}
            return
        if config_path is None:
            role_results[role] = {"status": "skipped", "reason": "not configured"}
            return
        resolved_path = config_path if config_path.is_absolute() else main_path.parent / config_path
        try:
            role_results[role] = {"status": "ok", "payload": probe(str(resolved_path))}
        except WIZARD_ROLE_PROBE_ERRORS as exc:
            ok = False
            role_results[role] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    run_role("meter", runtime.meter_config_path, probe_meter_backend)
    run_role("switch", runtime.switch_config_path, probe_switch_backend)
    run_role("charger", runtime.charger_config_path, read_charger_backend)
    checked_roles = tuple(role for role, payload in role_results.items() if payload.get("status") != "skipped")
    return {
        "ok": ok,
        "checked_roles": checked_roles,
        "roles": role_results,
    }


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
