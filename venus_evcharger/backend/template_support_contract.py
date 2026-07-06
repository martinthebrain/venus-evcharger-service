# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared template-backend HTTP/config contract constants."""

from __future__ import annotations

from typing import Final

AUTH_FALLBACK_FLAG: Final = "_adapter_auth_fallback_enabled"
SERVICE_USERNAME_ATTR: Final = "username"
SERVICE_PASSWORD_ATTR: Final = "password"
SERVICE_DIGEST_AUTH_ATTR: Final = "use_digest_auth"

ADAPTER_USERNAME_KEY: Final = "Username"
ADAPTER_PASSWORD_KEY: Final = "Password"
ADAPTER_DIGEST_AUTH_KEY: Final = "DigestAuth"
ADAPTER_AUTH_HEADER_NAME_KEY: Final = "AuthHeaderName"
ADAPTER_AUTH_HEADER_VALUE_KEY: Final = "AuthHeaderValue"

TEMPLATE_BACKEND_LABEL: Final = "template backend"
SUPPORTED_HTTP_METHODS: Final = frozenset(("GET", "POST", "PUT", "PATCH"))
ABSOLUTE_URL_MARKER: Final = "://"
URL_SEPARATOR: Final = "/"
