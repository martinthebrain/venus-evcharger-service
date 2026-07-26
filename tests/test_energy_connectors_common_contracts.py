# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for helpers shared by external energy connectors."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from venus_evcharger.backend.template_support import TemplateAuthSettings
from venus_evcharger.energy.connectors_common import (
    EnergyConnectorRuntimeState,
    EnergySourceHttpClient,
    _bounded_request_timeout_seconds,
    _csv_filter,
    _normalized_connector_type,
    _normalized_optional_bool_value,
    _optional_bool_path,
    _optional_confidence_path,
    _optional_float_path,
    _optional_path,
    _optional_text_path,
    _runtime_cache_get,
    _runtime_cache_pop,
    _runtime_cache_put,
    _runtime_default_timeout_seconds,
    _runtime_owner,
    _runtime_state,
    _sum_optional,
)


class _Response:
    def __init__(self) -> None:
        self.headers = {"Content-Length": "12"}
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return {"value": 1}

    def iter_content(self, chunk_size: int) -> tuple[bytes, ...]:
        del chunk_size
        return (b'{"value": 1}',)

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self) -> None:
        self.url = ""
        self.timeout = 0.0
        self.kwargs: dict[str, object] = {}

    def get(self, *, url: str, timeout: float, **_kwargs: object) -> _Response:
        self.url = url
        self.timeout = timeout
        self.kwargs = dict(_kwargs)
        return _Response()


class _TimeoutRuntime:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.requests: list[float] = []
        self.session = _Session()

    def bounded_request_timeout_seconds(self, configured_seconds: float) -> float:
        self.requests.append(configured_seconds)
        return self.timeout_seconds


class EnergyConnectorsCommonContractTests(unittest.TestCase):
    def test_runtime_owner_and_typed_cache_state_are_exact(self) -> None:
        runtime = SimpleNamespace()
        owner = SimpleNamespace(service=runtime)
        self.assertIs(_runtime_owner(owner), runtime)
        self.assertIs(_runtime_owner(runtime), runtime)

        state = _runtime_state(runtime)
        self.assertIsInstance(state, EnergyConnectorRuntimeState)
        self.assertIs(_runtime_state(runtime), state)
        self.assertIs(runtime._energy_connector_runtime_state, state)

        _runtime_cache_put(runtime, "numbers", "valid", 3)
        _runtime_cache_put(runtime, "numbers", "invalid", "3")
        self.assertEqual(_runtime_cache_get(runtime, "numbers", "valid", int), 3)
        self.assertEqual(_runtime_cache_pop(runtime, "numbers", "valid"), 3)
        self.assertIsNone(_runtime_cache_pop(runtime, "absent", "key"))
        self.assertIsNone(_runtime_cache_pop(runtime, "numbers", "missing"))
        _runtime_cache_put(runtime, "numbers", "valid", 3)
        self.assertIsNone(_runtime_cache_get(runtime, "numbers", "invalid", int))
        self.assertIsNone(_runtime_cache_get(runtime, "numbers", "missing", int))
        self.assertEqual(state.caches, {"numbers": {"valid": 3}})

        second_runtime = SimpleNamespace()
        self.assertIsNone(_runtime_cache_get(second_runtime, "missing", "key", int))
        self.assertEqual(
            second_runtime._energy_connector_runtime_state.caches,
            {"missing": {}},
        )
        _runtime_cache_put(second_runtime, "numbers", "valid", 4)
        self.assertEqual(_runtime_cache_get(second_runtime, "numbers", "valid", int), 4)
        self.assertEqual(_runtime_cache_get(runtime, "numbers", "valid", int), 3)

    def test_deadline_limiter_and_http_client_restore_configured_timeout(self) -> None:
        self.assertEqual(_bounded_request_timeout_seconds(object(), 2.0), 2.0)
        self.assertEqual(_bounded_request_timeout_seconds(object(), 0.0), 0.001)
        self.assertEqual(_bounded_request_timeout_seconds(object(), -2.0), 0.001)

        runtime = _TimeoutRuntime(0.3)
        self.assertEqual(_bounded_request_timeout_seconds(runtime, 2.0), 0.3)
        runtime.timeout_seconds = 4.0
        self.assertEqual(_bounded_request_timeout_seconds(runtime, 2.0), 2.0)
        runtime.timeout_seconds = -1.0
        self.assertEqual(_bounded_request_timeout_seconds(runtime, 2.0), 0.001)
        self.assertEqual(runtime.requests, [2.0, 2.0, 2.0])

        runtime.timeout_seconds = 0.25
        runtime.requests.clear()
        auth_settings = TemplateAuthSettings(
            username="",
            password="",
            use_digest_auth=False,
            auth_header_name="Authorization",
            auth_header_value="Bearer test-token",
        )
        client = EnergySourceHttpClient(runtime, 5.0, auth_settings=auth_settings)
        self.assertIs(client.service, runtime)
        self.assertEqual(client.timeout_seconds, 5.0)
        self.assertIs(client.auth_settings, auth_settings)
        self.assertEqual(client.max_response_bytes, 262144)
        self.assertEqual(
            client._perform_request(
                "GET",
                "http://energy.local/${device}",
                context={"device": "meter"},
                json_template='{"source": "${device}"}',
            ),
            {"value": 1},
        )
        self.assertEqual(runtime.requests, [5.0])
        self.assertEqual(runtime.session.url, "http://energy.local/meter")
        self.assertEqual(runtime.session.timeout, 0.25)
        self.assertEqual(
            runtime.session.kwargs,
            {
                "headers": {"Authorization": "Bearer test-token"},
                "json": {"source": "meter"},
                "stream": True,
            },
        )
        self.assertEqual(client.timeout_seconds, 5.0)

    def test_timeout_default_contract_is_structural_and_exact(self) -> None:
        self.assertEqual(_runtime_default_timeout_seconds(object(), 2.0), 2.0)
        self.assertEqual(
            _runtime_default_timeout_seconds(
                SimpleNamespace(shelly_request_timeout_seconds=3.5),
                2.0,
            ),
            3.5,
        )
        self.assertEqual(
            _runtime_default_timeout_seconds(
                SimpleNamespace(shelly_request_timeout_seconds=0.0),
                2.0,
            ),
            2.0,
        )

    def test_connector_and_optional_path_normalization_is_exact(self) -> None:
        self.assertEqual(_normalized_connector_type(" TEMPLATE_HTTP "), "template_http")
        self.assertEqual(_normalized_connector_type(" MODBUS "), "modbus")
        self.assertEqual(_normalized_connector_type("  "), "")

        self.assertIsNone(_optional_path("  "))
        self.assertIsNone(_optional_path(None))
        self.assertEqual(_optional_path(" value.path "), "value.path")

        payload: dict[str, object] = {
            "number": "12.5",
            "invalid": "not-a-number",
            "text": " mode ",
            "empty": "  ",
            "null": None,
        }
        self.assertIsNone(_optional_float_path(payload, None))
        self.assertEqual(_optional_float_path(payload, "number"), 12.5)
        self.assertIsNone(_optional_float_path(payload, "invalid"))
        self.assertIsNone(_optional_text_path(payload, None))
        self.assertEqual(_optional_text_path(payload, "text"), "mode")
        self.assertIsNone(_optional_text_path(payload, "empty"))
        self.assertIsNone(_optional_text_path(payload, "null"))

    def test_optional_boolean_normalization_covers_every_input_family(self) -> None:
        for value, expected in (
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            (2, True),
            (0.5, False),
            (" TRUE ", True),
            ("false", False),
            ("yes", True),
            ("no", False),
            ("on", True),
            ("off", False),
            ("enabled", True),
            ("disabled", False),
        ):
            with self.subTest(value=value):
                self.assertIs(_normalized_optional_bool_value(value), expected)

        for value in (None, object(), "1", "unexpected", ""):
            with self.subTest(unsupported=value):
                self.assertIsNone(_normalized_optional_bool_value(value))

        payload: dict[str, object] = {
            "direct": "enabled",
            "numeric_string": "1",
            "unknown": "unexpected",
        }
        self.assertIsNone(_optional_bool_path(payload, None))
        self.assertIs(_optional_bool_path(payload, "direct"), True)
        self.assertIs(_optional_bool_path(payload, "numeric_string"), True)
        self.assertIs(_optional_bool_path(payload, "unknown"), False)

    def test_confidence_sum_and_csv_boundaries_are_exact(self) -> None:
        payload: dict[str, object] = {
            "low": -0.1,
            "zero": 0.0,
            "middle": 0.25,
            "one": 1.0,
            "high": 1.1,
            "invalid": "invalid",
        }
        self.assertIsNone(_optional_confidence_path(payload, None))
        self.assertIsNone(_optional_confidence_path(payload, "invalid"))
        self.assertEqual(_optional_confidence_path(payload, "low"), 0.0)
        self.assertEqual(_optional_confidence_path(payload, "zero"), 0.0)
        self.assertEqual(_optional_confidence_path(payload, "middle"), 0.25)
        self.assertEqual(_optional_confidence_path(payload, "one"), 1.0)
        self.assertEqual(_optional_confidence_path(payload, "high"), 1.0)

        self.assertIsNone(_sum_optional((None, None)))
        self.assertEqual(_sum_optional((None, 1, 2.5)), 3.5)
        self.assertEqual(_sum_optional((0.0,)), 0.0)

        self.assertEqual(_csv_filter(None), ())
        self.assertEqual(_csv_filter("  "), ())
        self.assertEqual(_csv_filter(" first, , second ,third "), ("first", "second", "third"))


if __name__ == "__main__":
    unittest.main()
