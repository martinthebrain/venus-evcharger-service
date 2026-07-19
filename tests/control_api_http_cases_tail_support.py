# SPDX-License-Identifier: GPL-3.0-or-later
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.control_api_http_cases_common import _FakeHandler, control_api_http_service
from venus_evcharger.control import ControlApiRateLimiter, ControlCommand, ControlResult, LocalControlApiHttpServer
from venus_evcharger.control.http_api_auth import (
    INSUFFICIENT_SCOPE_ERROR,
    UNAUTHORIZED_ERROR,
    ControlApiHttpAuthenticator,
)
from venus_evcharger.control.http_api_command_payloads import (
    http_status_for_result,
    payload_error_code,
    result_error_code,
)
from venus_evcharger.control.http_api_events import ControlApiHttpEventEndpoint
from venus_evcharger.control.http_api_response import ControlApiHttpResponder
from venus_evcharger.control.http_api_routing import ControlApiHttpRouter


__all__ = [name for name in globals() if not name.startswith("__")]
