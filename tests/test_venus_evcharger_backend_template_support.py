# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    TemplateHttpBackendBase,
    _request_auth,
    _request_headers,
    _request_method_callable,
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


class _TemplateBackend(TemplateHttpBackendBase):
    pass


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

        session = SimpleNamespace(get="get", post="post", put="put", patch="patch")
        self.assertEqual(_request_method_callable(session, "GET"), "get")
        self.assertEqual(_request_method_callable(session, "POST"), "post")
        self.assertEqual(_request_method_callable(session, "PUT"), "put")
        self.assertEqual(_request_method_callable(session, "PATCH"), "patch")
        with self.assertRaisesRegex(ValueError, "Unsupported template backend HTTP method"):
            _request_method_callable(session, "DELETE")

        response = MagicMock()
        response.json.return_value = ["not", "a", "dict"]
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
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"ok": True}
        session.post.return_value = response
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
        session.post.assert_called_once_with(
            url="http://adapter.local/control",
            timeout=2.0,
            json={"enabled": True},
        )
