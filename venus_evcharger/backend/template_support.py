# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared HTTP/JSON helpers for template-backed meter, switch, and charger backends."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import json
from string import Template
from typing import Any
from urllib.parse import urljoin

import requests
from requests.auth import HTTPDigestAuth

from .config_file import config_section, load_required_backend_config
from .template_support_contract import (
    ABSOLUTE_URL_MARKER,
    ADAPTER_AUTH_HEADER_NAME_KEY,
    ADAPTER_AUTH_HEADER_VALUE_KEY,
    ADAPTER_DIGEST_AUTH_KEY,
    ADAPTER_PASSWORD_KEY,
    ADAPTER_USERNAME_KEY,
    AUTH_FALLBACK_FLAG,
    SERVICE_DIGEST_AUTH_ATTR,
    SERVICE_PASSWORD_ATTR,
    SERVICE_USERNAME_ATTR,
    SUPPORTED_HTTP_METHODS,
    TEMPLATE_BACKEND_LABEL,
    URL_SEPARATOR,
)
from venus_evcharger.core.contracts import normalize_binary_flag


@dataclass(frozen=True)
class TemplateAuthSettings:
    """Optional per-request auth settings for template-backed HTTP adapters."""

    username: str
    password: str
    use_digest_auth: bool
    auth_header_name: str | None
    auth_header_value: str | None


def load_template_auth_settings(
    adapter: configparser.SectionProxy,
    service: object | None = None,
) -> TemplateAuthSettings:
    """Return normalized auth settings shared by all template backends."""
    auth_fallback_enabled = _service_bool_attr(service, AUTH_FALLBACK_FLAG)
    username = str(
        adapter.get(
            ADAPTER_USERNAME_KEY,
            getattr(service, SERVICE_USERNAME_ATTR, "") if auth_fallback_enabled else "",
        )
    ).strip()
    password = str(
        adapter.get(
            ADAPTER_PASSWORD_KEY,
            getattr(service, SERVICE_PASSWORD_ATTR, "") if auth_fallback_enabled else "",
        )
    )
    use_digest_auth = _template_digest_auth_enabled(
        adapter,
        auth_fallback_enabled and _service_bool_attr(service, SERVICE_DIGEST_AUTH_ATTR),
    )
    auth_header_name = _optional_text(adapter.get(ADAPTER_AUTH_HEADER_NAME_KEY, ""))
    auth_header_value = _optional_text(adapter.get(ADAPTER_AUTH_HEADER_VALUE_KEY, ""))
    _validate_template_auth_settings(
        username=username,
        use_digest_auth=use_digest_auth,
        auth_header_name=auth_header_name,
        auth_header_value=auth_header_value,
    )
    return TemplateAuthSettings(
        username=username,
        password=password,
        use_digest_auth=use_digest_auth,
        auth_header_name=auth_header_name,
        auth_header_value=auth_header_value,
    )


def _service_bool_attr(service: object | None, attr: str) -> bool:
    """Return one optional service boolean without implicit truthy defaults."""
    if service is None or not hasattr(service, attr):
        return False
    return bool(getattr(service, attr))


def _template_digest_auth_enabled(adapter: configparser.SectionProxy, fallback: bool) -> bool:
    """Return DigestAuth from adapter config or an explicit service fallback."""
    raw = adapter.get(ADAPTER_DIGEST_AUTH_KEY)
    if raw is None:
        return bool(fallback)
    return bool(normalize_binary_flag(raw))


def _optional_text(value: object) -> str | None:
    """Return a trimmed optional string."""
    return str(value).strip() or None


def _validate_template_auth_settings(
    *,
    username: str,
    use_digest_auth: bool,
    auth_header_name: str | None,
    auth_header_value: str | None,
) -> None:
    """Raise when one template auth combination is incomplete or invalid."""
    if use_digest_auth and not username:
        raise ValueError("Template backend DigestAuth requires Adapter.Username")
    if (auth_header_name is None) != (auth_header_value is None):
        raise ValueError(
            "Template backend auth header requires both Adapter.AuthHeaderName and Adapter.AuthHeaderValue"
        )


def load_template_config(config_path: str) -> configparser.ConfigParser:
    """Load one template backend config file."""
    return load_required_backend_config(config_path, TEMPLATE_BACKEND_LABEL)


def normalize_http_method(value: object, default: str) -> str:
    """Return one supported HTTP method."""
    if value is None:
        return default
    method = str(value).strip().upper()
    return method if method in SUPPORTED_HTTP_METHODS else default


def resolved_url(base_url: str, raw_url: object) -> str:
    """Return one absolute URL using the optional adapter base URL."""
    url = str(raw_url).strip() if raw_url is not None else ""
    if not url:
        return ""
    if ABSOLUTE_URL_MARKER in url:
        return url
    if not base_url:
        raise ValueError(f"Relative URL '{url}' requires Adapter.BaseUrl")
    return urljoin(_base_url_prefix(base_url), _relative_url_path(url))


def _base_url_prefix(base_url: str) -> str:
    """Return one base URL ending with exactly one path separator."""
    normalized = str(base_url)
    while normalized.endswith(URL_SEPARATOR):
        normalized = normalized[:-1]
    return f"{normalized}{URL_SEPARATOR}"


def _relative_url_path(url: str) -> str:
    """Return one relative URL path without leading separators."""
    normalized = str(url)
    while normalized.startswith(URL_SEPARATOR):
        normalized = normalized[1:]
    return normalized


def json_path_value(payload: dict[str, object], path: str) -> object:
    """Return one nested JSON value addressed by a dotted path."""
    current: object = payload
    for part in str(path).split("."):
        token = part.strip()
        if not token:
            continue
        if not isinstance(current, dict) or token not in current:
            raise ValueError(f"Missing response path '{path}'")
        current = current[token]
    return current


def payload_object(payload: object) -> dict[str, object]:
    """Return one typed JSON object payload for dotted-path lookups."""
    if not isinstance(payload, dict):
        raise ValueError("Template backend response must be a JSON object")
    return {str(key): value for key, value in payload.items()}


def enabled_state_from_text(text: str) -> bool | None:
    """Return an optional enabled-state from normalized text tokens."""
    if text in {"1", "true", "on", "yes", "enabled"}:
        return True
    if text in {"0", "false", "off", "no", "disabled"}:
        return False
    return None


def render_json_payload(template_text: str | None, context: dict[str, str]) -> object | None:
    """Render one optional JSON body template."""
    if not template_text:
        return None
    rendered = Template(template_text).safe_substitute(context).strip()
    if not rendered:
        return None
    payload: object = json.loads(rendered)
    return payload


def _request_kwargs(
    url: str,
    timeout_seconds: float,
    payload: object | None,
    auth_settings: TemplateAuthSettings,
) -> dict[str, object]:
    """Return requests kwargs for one template backend HTTP call."""
    kwargs: dict[str, object] = {
        "url": str(url),
        "timeout": float(timeout_seconds),
    }
    if payload is not None:
        kwargs["json"] = payload
    auth = _request_auth(auth_settings)
    if auth is not None:
        kwargs["auth"] = auth
    headers = _request_headers(auth_settings)
    if headers is not None:
        kwargs["headers"] = headers
    return kwargs


def _request_auth(auth_settings: TemplateAuthSettings) -> object | None:
    """Return one optional requests-compatible auth object."""
    if not auth_settings.username:
        return None
    if auth_settings.use_digest_auth:
        auth: object = HTTPDigestAuth(auth_settings.username, auth_settings.password)
        return auth
    return (auth_settings.username, auth_settings.password)


def _request_headers(auth_settings: TemplateAuthSettings) -> dict[str, str] | None:
    """Return optional extra headers injected into every template-backend request."""
    if auth_settings.auth_header_name is None or auth_settings.auth_header_value is None:
        return None
    return {
        auth_settings.auth_header_name: auth_settings.auth_header_value,
    }


def _request_method_callable(session: Any, method: str) -> Any:
    """Return the bound requests-session method for one normalized HTTP verb."""
    normalized_method = str(method).strip().upper()
    if normalized_method == "GET":
        return session.get
    if normalized_method == "POST":
        return session.post
    if normalized_method == "PUT":
        return session.put
    if normalized_method == "PATCH":
        return session.patch
    raise ValueError(f"Unsupported template backend HTTP method '{method}'")


def _response_payload_dict(response: Any) -> dict[str, object]:
    """Return a dict payload from one HTTP response, or an empty dict otherwise."""
    response_payload = response.json()
    return {str(key): value for key, value in response_payload.items()} if isinstance(response_payload, dict) else {}


class TemplateHttpBackendBase:
    """Small shared HTTP client helper for template backends."""

    def __init__(
        self,
        service: object,
        timeout_seconds: float,
        *,
        auth_settings: TemplateAuthSettings | None = None,
    ) -> None:
        self.service = service
        self.timeout_seconds = float(timeout_seconds)
        self.auth_settings = auth_settings or TemplateAuthSettings(
            username="",
            password="",
            use_digest_auth=False,
            auth_header_name=None,
            auth_header_value=None,
        )
        session = getattr(service, "session", None)
        self._session = session if session is not None else requests.Session()

    def _perform_request(
        self,
        method: str,
        url: str,
        *,
        context: dict[str, str] | None = None,
        json_template: str | None = None,
    ) -> dict[str, object]:
        """Perform one backend HTTP request and return a dict payload when available."""
        template_context = context or {}
        rendered_url = str(Template(url).safe_substitute(template_context))
        payload = render_json_payload(json_template, template_context)
        kwargs = _request_kwargs(rendered_url, self.timeout_seconds, payload, self.auth_settings)
        response = _request_method_callable(self._session, method)(**kwargs)
        response.raise_for_status()
        return _response_payload_dict(response)


__all__ = [
    "TemplateAuthSettings",
    "TemplateHttpBackendBase",
    "config_section",
    "enabled_state_from_text",
    "json_path_value",
    "load_required_backend_config",
    "load_template_auth_settings",
    "load_template_config",
    "normalize_http_method",
    "payload_object",
    "render_json_payload",
    "resolved_url",
]
