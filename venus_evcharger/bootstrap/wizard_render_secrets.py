# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
from types import SimpleNamespace

from venus_evcharger.bootstrap.wizard_render_text import CasePreservingConfigParser


SENSITIVE_ASSIGNMENT_KEYS = frozenset(
    (
        "Password",
        "ControlApiAuthToken",
        "ControlApiReadToken",
        "ControlApiControlToken",
        "ControlApiAdminToken",
        "ControlApiUpdateToken",
    )
)
DIGEST_AUTH_TRUE_VALUES = frozenset(("1", "true", "yes", "on"))

KEY_HOST = "Host"
KEY_USERNAME = "Username"
KEY_PASSWORD = "Password"
KEY_DIGEST_AUTH = "DigestAuth"
KEY_CONTROL_API_AUTH_TOKEN = "ControlApiAuthToken"
KEY_SHELLY_REQUEST_TIMEOUT = "ShellyRequestTimeoutSeconds"
KEY_SHELLY_COMPONENT = "ShellyComponent"
KEY_SHELLY_ID = "ShellyId"
KEY_PHASE = "Phase"
KEY_MAX_CURRENT = "MaxCurrent"

DEFAULT_DIGEST_AUTH = "0"
DEFAULT_SHELLY_TIMEOUT_SECONDS = 2.0
DEFAULT_SHELLY_COMPONENT = "Switch"
DEFAULT_SHELLY_ID = 0
DEFAULT_PHASE = "L1"
DEFAULT_MAX_CURRENT = 16.0


def redact_sensitive_assignments(text: str) -> str:
    """Remove secret assignment values before temporary wizard materialization."""
    redacted: list[str] = []
    for line in text.splitlines():
        key, separator, _value = line.partition("=")
        if separator and key.strip() in SENSITIVE_ASSIGNMENT_KEYS:
            continue
        redacted.append(line)
    return "\n".join(redacted) + ("\n" if text.endswith("\n") else "")


def sensitive_defaults_from_config_text(config_text: str) -> dict[str, str]:
    parser = CasePreservingConfigParser()
    try:
        parser.read_string(config_text)
    except configparser.MissingSectionHeaderError:
        parser.read_string("[DEFAULT]\n" + config_text)
    defaults = parser["DEFAULT"]
    return {
        KEY_USERNAME: _stripped_default(defaults, KEY_USERNAME),
        KEY_PASSWORD: _stripped_default(defaults, KEY_PASSWORD),
        KEY_DIGEST_AUTH: _stripped_default(defaults, KEY_DIGEST_AUTH, DEFAULT_DIGEST_AUTH),
        KEY_CONTROL_API_AUTH_TOKEN: _stripped_default(defaults, KEY_CONTROL_API_AUTH_TOKEN),
    }


def redact_sensitive_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
) -> tuple[str, dict[str, str]]:
    return (
        redact_sensitive_assignments(config_text),
        {relative_path: redact_sensitive_assignments(content) for relative_path, content in adapter_files.items()},
    )


def secret_default(
    defaults: configparser.SectionProxy,
    secret_defaults: dict[str, str] | None,
    key: str,
    fallback: str = "",
) -> str:
    if secret_defaults is not None and key in secret_defaults and secret_defaults[key]:
        return secret_defaults[key]
    return _stripped_default(defaults, key, fallback)


def _stripped_default(defaults: configparser.SectionProxy, key: str, fallback: str = "") -> str:
    return defaults.get(key, fallback).strip()


def _float_default(defaults: configparser.SectionProxy, key: str, fallback: float) -> float:
    if key not in defaults:
        return fallback
    value = defaults[key].strip()
    return float(value) if value else fallback


def _int_default(defaults: configparser.SectionProxy, key: str, fallback: int) -> int:
    if key not in defaults:
        return fallback
    value = defaults[key].strip()
    return int(value) if value else fallback


def probe_service_from_wallbox_config(
    parser: configparser.ConfigParser,
    secret_defaults: dict[str, str] | None = None,
) -> object:
    defaults = parser["DEFAULT"]
    digest_auth = secret_default(defaults, secret_defaults, KEY_DIGEST_AUTH).lower()
    return SimpleNamespace(
        config=parser,
        session=None,
        host=_stripped_default(defaults, KEY_HOST),
        username=secret_default(defaults, secret_defaults, KEY_USERNAME),
        password=secret_default(defaults, secret_defaults, KEY_PASSWORD),
        use_digest_auth=digest_auth in DIGEST_AUTH_TRUE_VALUES,
        shelly_request_timeout_seconds=_float_default(defaults, KEY_SHELLY_REQUEST_TIMEOUT, DEFAULT_SHELLY_TIMEOUT_SECONDS),
        pm_component=_stripped_default(defaults, KEY_SHELLY_COMPONENT, DEFAULT_SHELLY_COMPONENT),
        pm_id=_int_default(defaults, KEY_SHELLY_ID, DEFAULT_SHELLY_ID),
        phase=_stripped_default(defaults, KEY_PHASE, DEFAULT_PHASE),
        max_current=_float_default(defaults, KEY_MAX_CURRENT, DEFAULT_MAX_CURRENT),
        _last_voltage=None,
        _adapter_auth_fallback_enabled=secret_defaults is not None,
    )


_secret_default = secret_default
_probe_service_from_wallbox_config = probe_service_from_wallbox_config
