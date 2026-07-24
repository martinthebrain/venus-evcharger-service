# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    TemplateHttpBackendBase,
    _request_auth,
    _request_headers,
    _request_kwargs,
    _response_payload_dict,
    enabled_state_from_text,
    json_path_value,
    load_template_auth_settings,
    load_template_config,
    normalize_http_method,
    payload_object,
    render_json_payload,
    resolved_url,
)
from venus_evcharger.backend.template_http_transport import request_method_callable


class _TemplateBackend(TemplateHttpBackendBase):
    pass


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> object:
        return self.payload


class _Session:
    def __init__(self, response: _Response | None = None) -> None:
        self.response = response or _Response({})
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _request(self, method: str, **kwargs: object) -> _Response:
        self.calls.append((method, kwargs))
        return self.response

    def get(self, **kwargs: object) -> _Response:
        return self._request("GET", **kwargs)

    def post(self, **kwargs: object) -> _Response:
        return self._request("POST", **kwargs)

    def put(self, **kwargs: object) -> _Response:
        return self._request("PUT", **kwargs)

    def patch(self, **kwargs: object) -> _Response:
        return self._request("PATCH", **kwargs)


class _InvalidDynamicSession:
    def __getattr__(self, name: str) -> object:
        return name


class TestShellyWallboxBackendTemplateSupport(unittest.TestCase):
    @staticmethod
    def _adapter(**values: object) -> configparser.SectionProxy:
        parser = configparser.ConfigParser()
        parser.read_dict({"Adapter": {key: str(value) for key, value in values.items()}})
        return parser["Adapter"]

    def test_template_auth_and_config_helpers_cover_validation_edges(self) -> None:
        with self.assertRaises(ValueError) as digest_context:
            load_template_auth_settings(self._adapter(DigestAuth=1))
        self.assertEqual(str(digest_context.exception), "Template backend DigestAuth requires Adapter.Username")
        with self.assertRaises(ValueError) as header_context:
            load_template_auth_settings(self._adapter(AuthHeaderName="Authorization"))
        self.assertEqual(
            str(header_context.exception),
            "Template backend auth header requires both Adapter.AuthHeaderName and Adapter.AuthHeaderValue",
        )
        with self.assertRaises(FileNotFoundError):
            load_template_config("/definitely/missing.ini")

    def test_template_auth_settings_use_service_fallbacks_only_when_enabled(self) -> None:
        service = SimpleNamespace(
            _adapter_auth_fallback_enabled=True,
            username="service-user",
            password="service-secret",
            use_digest_auth=True,
        )

        settings = load_template_auth_settings(self._adapter(), service)

        self.assertEqual(settings.username, "service-user")
        self.assertEqual(settings.password, "service-secret")
        self.assertTrue(settings.use_digest_auth)

        missing_service_values = load_template_auth_settings(
            self._adapter(),
            SimpleNamespace(_adapter_auth_fallback_enabled=True),
        )

        self.assertEqual(missing_service_values.username, "")
        self.assertEqual(missing_service_values.password, "")
        self.assertFalse(missing_service_values.use_digest_auth)

        disabled = load_template_auth_settings(
            self._adapter(),
            SimpleNamespace(
                _adapter_auth_fallback_enabled=False,
                username="ignored-user",
                password="ignored-secret",
                use_digest_auth=True,
            ),
        )

        self.assertEqual(disabled.username, "")
        self.assertEqual(disabled.password, "")
        self.assertFalse(disabled.use_digest_auth)

        default_disabled = load_template_auth_settings(
            self._adapter(),
            SimpleNamespace(username="ignored-user", password="ignored-secret", use_digest_auth=True),
        )

        self.assertEqual(default_disabled.username, "")
        self.assertEqual(default_disabled.password, "")
        self.assertFalse(default_disabled.use_digest_auth)

    def test_template_url_json_and_request_helpers_cover_error_paths(self) -> None:
        self.assertEqual(resolved_url("http://base.local", "http://other.local/x"), "http://other.local/x")
        self.assertEqual(resolved_url("", "http://other.local/x"), "http://other.local/x")
        self.assertEqual(resolved_url("http://base.local/root///", "/nested/value"), "http://base.local/root/nested/value")
        self.assertEqual(resolved_url("http://base.local", None), "")
        with self.assertRaisesRegex(ValueError, "requires Adapter.BaseUrl"):
            resolved_url("", "/relative")
        with self.assertRaisesRegex(ValueError, "Missing response path"):
            json_path_value({"outer": {}}, "outer.missing")
        self.assertEqual(json_path_value({"outer": {"x": 1}}, "outer..x"), 1)
        with self.assertRaises(ValueError) as payload_context:
            payload_object(["not", "a", "dict"])
        self.assertEqual(str(payload_context.exception), "Template backend response must be a JSON object")
        self.assertEqual(payload_object({7: "seven"}), {"7": "seven"})
        self.assertIsNone(render_json_payload(None, {}))
        self.assertIsNone(render_json_payload("   ", {}))
        self.assertEqual(normalize_http_method(" patch ", "GET"), "PATCH")
        self.assertEqual(normalize_http_method("DELETE", "POST"), "POST")
        self.assertEqual(normalize_http_method(None, "PUT"), "PUT")

        digest_settings = TemplateAuthSettings("user", "secret", True, None, None)
        basic_settings = TemplateAuthSettings("user", "secret", False, None, None)
        header_settings = TemplateAuthSettings("", "", False, "Authorization", "Bearer token")
        incomplete_header_settings = TemplateAuthSettings("", "", False, "X-Test", None)
        with patch("venus_evcharger.backend.template_support.HTTPDigestAuth", return_value="digest-auth"):
            self.assertEqual(_request_auth(digest_settings), "digest-auth")
        self.assertEqual(_request_auth(basic_settings), ("user", "secret"))
        self.assertIsNone(_request_auth(TemplateAuthSettings("", "", False, None, None)))
        self.assertEqual(_request_headers(header_settings), {"Authorization": "Bearer token"})
        self.assertIsNone(_request_headers(digest_settings))
        self.assertIsNone(_request_headers(incomplete_header_settings))
        self.assertEqual(
            _request_kwargs("http://test.local", 2.5, {"enabled": True}, basic_settings),
            {
                "url": "http://test.local",
                "timeout": 2.5,
                "json": {"enabled": True},
                "auth": ("user", "secret"),
            },
        )
        self.assertEqual(
            _request_kwargs("http://test.local", 2.5, None, header_settings),
            {
                "url": "http://test.local",
                "timeout": 2.5,
                "headers": {"Authorization": "Bearer token"},
            },
        )

        session = _Session()
        for method in ("GET", "POST", "PUT", "PATCH"):
            response = request_method_callable(session, method)(url="http://test.local", timeout=1.0)
            self.assertEqual(response.json(), {})
        with self.assertRaisesRegex(ValueError, "Unsupported template backend HTTP method"):
            request_method_callable(session, "DELETE")
        with self.assertRaisesRegex(TypeError, "does not implement HTTP GET"):
            request_method_callable(object(), "GET")
        with self.assertRaisesRegex(TypeError, "does not implement HTTP POST"):
            request_method_callable(object(), "POST")
        with self.assertRaisesRegex(TypeError, "does not implement HTTP PUT"):
            request_method_callable(object(), "PUT")
        with self.assertRaisesRegex(TypeError, "does not implement HTTP PATCH"):
            request_method_callable(object(), "PATCH")
        with self.assertRaisesRegex(TypeError, "does not implement HTTP GET"):
            request_method_callable(_InvalidDynamicSession(), "GET")

        response = _Response(["not", "a", "dict"])
        self.assertEqual(_response_payload_dict(response), {})

    def test_template_enabled_state_tokens_are_exhaustive_contract(self) -> None:
        for token in ("1", "true", "on", "yes", "enabled"):
            self.assertIs(enabled_state_from_text(token), True)
        for token in ("0", "false", "off", "no", "disabled"):
            self.assertIs(enabled_state_from_text(token), False)
        self.assertIsNone(enabled_state_from_text("unknown"))

    def test_template_http_backend_default_initialization_is_explicit(self) -> None:
        service = SimpleNamespace()
        backend = _TemplateBackend(service, 1.25)

        self.assertIs(backend.service, service)
        self.assertEqual(backend.timeout_seconds, 1.25)
        self.assertEqual(backend.auth_settings, TemplateAuthSettings("", "", False, None, None))
        self.assertIsNotNone(backend._session)

    def test_template_http_backend_perform_request_renders_templates(self) -> None:
        response = _Response({"ok": True})
        session = _Session(response)
        backend = _TemplateBackend(
            SimpleNamespace(session=session),
            2.0,
            auth_settings=TemplateAuthSettings("", "", False, None, None),
        )

        payload = backend._perform_request(
            "POST",
            "http://adapter.local/$endpoint",
            context={"endpoint": "control", "enabled_json": "true"},
            json_template='{"enabled": $enabled_json}',
        )

        self.assertEqual(payload, {"ok": True})
        self.assertTrue(response.raise_called)
        self.assertEqual(
            session.calls,
            [
                (
                    "POST",
                    {
                        "url": "http://adapter.local/control",
                        "timeout": 2.0,
                        "json": {"enabled": True},
                    },
                )
            ],
        )
