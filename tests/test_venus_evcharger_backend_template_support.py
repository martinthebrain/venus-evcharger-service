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
    json_path_value,
    load_template_auth_settings,
    load_template_config,
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
        with self.assertRaisesRegex(ValueError, "DigestAuth requires Adapter.Username"):
            load_template_auth_settings(self._adapter(DigestAuth=1))
        with self.assertRaisesRegex(ValueError, "requires both Adapter.AuthHeaderName and Adapter.AuthHeaderValue"):
            load_template_auth_settings(self._adapter(AuthHeaderName="Authorization"))
        with self.assertRaises(FileNotFoundError):
            load_template_config("/definitely/missing.ini")

    def test_template_url_json_and_request_helpers_cover_error_paths(self) -> None:
        self.assertEqual(resolved_url("http://base.local", "http://other.local/x"), "http://other.local/x")
        with self.assertRaisesRegex(ValueError, "requires Adapter.BaseUrl"):
            resolved_url("", "/relative")
        with self.assertRaisesRegex(ValueError, "Missing response path"):
            json_path_value({"outer": {}}, "outer.missing")
        self.assertEqual(json_path_value({"outer": {"x": 1}}, "outer..x"), 1)
        with self.assertRaisesRegex(ValueError, "Template backend response must be a JSON object"):
            payload_object(["not", "a", "dict"])
        self.assertIsNone(render_json_payload(None, {}))
        self.assertIsNone(render_json_payload("   ", {}))

        digest_settings = TemplateAuthSettings("user", "secret", True, None, None)
        basic_settings = TemplateAuthSettings("user", "secret", False, None, None)
        header_settings = TemplateAuthSettings("", "", False, "Authorization", "Bearer token")
        with patch("venus_evcharger.backend.template_support.HTTPDigestAuth", return_value="digest-auth"):
            self.assertEqual(_request_auth(digest_settings), "digest-auth")
        self.assertEqual(_request_auth(basic_settings), ("user", "secret"))
        self.assertIsNone(_request_auth(TemplateAuthSettings("", "", False, None, None)))
        self.assertEqual(_request_headers(header_settings), {"Authorization": "Bearer token"})
        self.assertIsNone(_request_headers(digest_settings))

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
