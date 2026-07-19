# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared HTTP/JSON helpers for template-backed meter, switch, and charger backends."""

from __future__ import annotations

import configparser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from string import Template
from typing import Protocol, TypeGuard, TypedDict, runtime_checkable
from urllib.parse import urljoin

import requests
from requests.auth import AuthBase, HTTPDigestAuth

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


RequestAuth = tuple[str, str] | AuthBase


class _RequiredRequestKwargs(TypedDict):
    url: str
    timeout: float


class TemplateRequestKwargs(_RequiredRequestKwargs, total=False):
    """Keyword arguments accepted by one template HTTP request."""

    json: object
    auth: RequestAuth
    headers: dict[str, str]


@runtime_checkable
class HttpResponse(Protocol):
    """Minimal response contract required by HTTP-backed adapters."""

    def raise_for_status(self) -> None:
        """Raise when the HTTP request was unsuccessful."""

    def json(self) -> object:
        """Return the decoded JSON boundary value."""


class HttpRequestCallable(Protocol):
    """Callable contract shared by supported HTTP verbs."""

    def __call__(
        self,
        *,
        url: str,
        timeout: float,
        json: object = ...,
        params: dict[str, str] = ...,
        auth: RequestAuth = ...,
        headers: dict[str, str] = ...,
    ) -> HttpResponse:
        """Execute one HTTP request."""
        ...


@runtime_checkable
class HttpGetSession(Protocol):
    """Session surface for HTTP GET requests."""

    def get(
        self,
        *,
        url: str,
        timeout: float,
        json: object = ...,
        params: dict[str, str] = ...,
        auth: RequestAuth = ...,
        headers: dict[str, str] = ...,
    ) -> HttpResponse: ...


@runtime_checkable
class HttpPostSession(Protocol):
    """Session surface for HTTP POST requests."""

    def post(
        self,
        *,
        url: str,
        timeout: float,
        json: object = ...,
        params: dict[str, str] = ...,
        auth: RequestAuth = ...,
        headers: dict[str, str] = ...,
    ) -> HttpResponse: ...


@runtime_checkable
class HttpPutSession(Protocol):
    """Session surface for HTTP PUT requests."""

    def put(
        self,
        *,
        url: str,
        timeout: float,
        json: object = ...,
        params: dict[str, str] = ...,
        auth: RequestAuth = ...,
        headers: dict[str, str] = ...,
    ) -> HttpResponse: ...


@runtime_checkable
class HttpPatchSession(Protocol):
    """Session surface for HTTP PATCH requests."""

    def patch(
        self,
        *,
        url: str,
        timeout: float,
        json: object = ...,
        params: dict[str, str] = ...,
        auth: RequestAuth = ...,
        headers: dict[str, str] = ...,
    ) -> HttpResponse: ...


@runtime_checkable
class DynamicHttpSession(Protocol):
    """Dynamic session surface used by injected proxy and test clients."""

    def __getattr__(self, name: str) -> object: ...  # pragma: no cover - structural protocol declaration


def object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """Narrow one dynamic JSON value to an object-keyed mapping."""
    return isinstance(value, Mapping)


def object_list(value: object) -> TypeGuard[list[object]]:
    """Narrow one dynamic JSON value to a list with object elements."""
    return isinstance(value, list)


def http_request_callable(value: object) -> TypeGuard[HttpRequestCallable]:
    """Narrow a dynamically supplied session member to a request callable."""
    return callable(value)


def normalized_object_mapping(value: object) -> dict[str, object] | None:
    """Normalize one dynamic mapping to the string-key JSON object contract."""
    if not object_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def http_session(value: object | None) -> object:
    """Return an injected session or a new requests session as a dynamic boundary value."""
    return _new_requests_session() if value is None else value


def _new_requests_session() -> object:
    """Construct a requests session behind the dynamic adapter boundary."""
    return requests.Session()


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


def json_path_value(payload: Mapping[str, object], path: str) -> object:
    """Return one nested JSON value addressed by a dotted path."""
    current: object = payload
    for part in str(path).split("."):
        token = part.strip()
        if not token:
            continue
        mapping = normalized_object_mapping(current)
        if mapping is None or token not in mapping:
            raise ValueError(f"Missing response path '{path}'")
        current = mapping[token]
    return current


def payload_object(payload: object) -> dict[str, object]:
    """Return one typed JSON object payload for dotted-path lookups."""
    normalized = normalized_object_mapping(payload)
    if normalized is None:
        raise ValueError("Template backend response must be a JSON object")
    return normalized


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
) -> TemplateRequestKwargs:
    """Return requests kwargs for one template backend HTTP call."""
    kwargs: TemplateRequestKwargs = {
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


def _request_auth(auth_settings: TemplateAuthSettings) -> RequestAuth | None:
    """Return one optional requests-compatible auth object."""
    if not auth_settings.username:
        return None
    if auth_settings.use_digest_auth:
        return HTTPDigestAuth(auth_settings.username, auth_settings.password)
    return (auth_settings.username, auth_settings.password)


def _request_headers(auth_settings: TemplateAuthSettings) -> dict[str, str] | None:
    """Return optional extra headers injected into every template-backend request."""
    if auth_settings.auth_header_name is None or auth_settings.auth_header_value is None:
        return None
    return {
        auth_settings.auth_header_name: auth_settings.auth_header_value,
    }


def _request_method_callable(session: object, method: str) -> HttpRequestCallable:
    """Return the bound requests-session method for one normalized HTTP verb."""
    normalized_method = str(method).strip().upper()
    resolver = _HTTP_METHOD_RESOLVERS.get(normalized_method)
    if resolver is None:
        raise ValueError(f"Unsupported template backend HTTP method '{method}'")
    return resolver(session)


def _get_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpGetSession):
        return session.get
    return _dynamic_request_callable(session, "get", "GET")


def _post_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpPostSession):
        return session.post
    return _dynamic_request_callable(session, "post", "POST")


def _put_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpPutSession):
        return session.put
    return _dynamic_request_callable(session, "put", "PUT")


def _patch_request_callable(session: object) -> HttpRequestCallable:
    if isinstance(session, HttpPatchSession):
        return session.patch
    return _dynamic_request_callable(session, "patch", "PATCH")


def _dynamic_request_callable(session: object, member_name: str, method: str) -> HttpRequestCallable:
    if not isinstance(session, DynamicHttpSession):
        raise TypeError(f"Template backend session does not implement HTTP {method}")
    candidate = session.__getattr__(member_name)
    if not http_request_callable(candidate):
        raise TypeError(f"Template backend session does not implement HTTP {method}")
    return candidate


_HTTP_METHOD_RESOLVERS: dict[str, Callable[[object], HttpRequestCallable]] = {
    "GET": _get_request_callable,
    "POST": _post_request_callable,
    "PUT": _put_request_callable,
    "PATCH": _patch_request_callable,
}


def _response_payload_dict(response: HttpResponse) -> dict[str, object]:
    """Return a dict payload from one HTTP response, or an empty dict otherwise."""
    return normalized_object_mapping(response.json()) or {}


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
        self._session = http_session(session)

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
