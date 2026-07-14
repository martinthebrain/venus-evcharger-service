# SPDX-License-Identifier: GPL-3.0-or-later
import json
import hashlib
import unittest

from venus_evcharger.control import build_control_api_openapi_spec
from venus_evcharger.control import openapi
from venus_evcharger.control import openapi_helpers


class TestVenusEvchargerControlOpenApi(unittest.TestCase):
    def test_complete_openapi_contract_has_stable_canonical_fingerprint(self) -> None:
        canonical = json.dumps(
            build_control_api_openapi_spec(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        self.assertEqual(len(canonical), 47948)
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            "f55eb9a400a2809d81999d5591b9d0dff7242f637430dcb9709f95dc4536ddb0",
        )

    def test_string_schema_supports_optional_enum_and_default(self) -> None:
        self.assertEqual(openapi._string_schema(), {"type": "string"})
        self.assertEqual(
            openapi._string_schema(enum=("b", "a"), default="a"),
            {"type": "string", "enum": ["a", "b"], "default": "a"},
        )
        self.assertEqual(
            openapi_helpers._string_schema(enum=(str(item) for item in (2, 1)), default="1"),
            {"type": "string", "enum": ["1", "2"], "default": "1"},
        )

    def test_boolean_schema_supports_optional_default(self) -> None:
        self.assertEqual(openapi._boolean_schema(), {"type": "boolean"})
        self.assertEqual(
            openapi._boolean_schema(default=True),
            {"type": "boolean", "default": True},
        )
        self.assertEqual(openapi._boolean_schema(default=False), {"type": "boolean", "default": False})

    def test_integer_schema_supports_optional_minimum(self) -> None:
        self.assertEqual(openapi._integer_schema(), {"type": "integer"})
        self.assertEqual(
            openapi._integer_schema(minimum=0),
            {"type": "integer", "minimum": 0},
        )
        self.assertEqual(
            openapi._integer_schema(enum=(2, 0, 1)),
            {"type": "integer", "enum": [0, 1, 2]},
        )
        self.assertEqual(
            openapi._integer_schema(minimum=1, enum=(3, 1, 2)),
            {"type": "integer", "minimum": 1, "enum": [1, 2, 3]},
        )

    def test_number_schema_supports_optional_minimum(self) -> None:
        self.assertEqual(openapi._number_schema(), {"type": "number"})
        self.assertEqual(
            openapi._number_schema(minimum=0.0),
            {"type": "number", "minimum": 0.0},
        )
        self.assertEqual(
            openapi._number_schema(exclusive_minimum=0.0, maximum=1.0),
            {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0},
        )
        self.assertEqual(
            openapi._number_schema(minimum=0.0, exclusive_minimum=0.5, maximum=16.0),
            {"type": "number", "minimum": 0.0, "exclusiveMinimum": 0.5, "maximum": 16.0},
        )

    def test_object_schema_and_named_command_schema_cover_optional_branches(self) -> None:
        item_schema = {"type": "string"}
        array_schema = openapi_helpers._array_schema(item_schema)
        item_schema["type"] = "integer"
        self.assertEqual(array_schema, {"type": "array", "items": {"type": "string"}})

        self.assertEqual(openapi_helpers._ref("ControlError"), {"$ref": "#/components/schemas/ControlError"})
        self.assertEqual(
            openapi_helpers._json_response("Problem", "ControlError"),
            {
                "description": "Problem",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ControlError"}}},
            },
        )
        self.assertEqual(
            openapi_helpers._const_schema("set_mode"),
            {"const": "set_mode", "type": "string"},
        )
        self.assertEqual(
            openapi_helpers._boolean_or_binary_integer_schema(),
            {"oneOf": [{"type": "boolean"}, {"type": "integer", "enum": [0, 1]}]},
        )
        self.assertEqual(
            openapi._object_schema({"a": {"type": "string"}}, required=(), additional_properties=False),
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            openapi._object_schema(
                {"a": {"type": "string"}},
                required=(field for field in ("a", "b")),
                additional_properties={"type": "string"},
            ),
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "additionalProperties": {"type": "string"},
                "required": ["a", "b"],
            },
        )
        self.assertEqual(
            openapi._object_schema({"a": {"type": "string"}}),
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            openapi_helpers._etag_headers(),
            {
                "ETag": {
                    "description": "Current local state token for optimistic concurrency.",
                    "schema": {"type": "string"},
                },
                "X-State-Token": {
                    "description": "Current local state token mirrored as a plain header value.",
                    "schema": {"type": "string"},
                },
            },
        )
        self.assertEqual(
            openapi._named_command_request_schema("set_mode", {"type": "integer"}),
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "const": "set_mode"},
                    "value": {"type": "integer"},
                    "detail": {"type": "string"},
                    "command_id": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
                "additionalProperties": False,
                "required": ["name", "value"],
            },
        )

    def test_openapi_spec_exposes_expected_paths_security_and_schemas(self) -> None:
        spec = build_control_api_openapi_spec()

        self.assertEqual(sorted(spec), ["components", "info", "openapi", "paths", "servers"])
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertEqual(
            spec["info"],
            {
                "title": "Venus EV Charger Service Local API",
                "version": "v1",
                "description": "Local command, state, and event API for the Venus EV charger service.",
            },
        )
        self.assertEqual(
            spec["servers"],
            [{"url": "http://127.0.0.1:8765"}, {"url": "http+unix://localhost"}],
        )

        paths = spec["paths"]
        self.assertIn("/v1/openapi.json", paths)
        self.assertIn("/v1/capabilities", paths)
        self.assertIn("/v1/control/command", paths)
        self.assertIn("/v1/events", paths)
        self.assertIn("/v1/state/healthz", paths)
        self.assertIn("/v1/state/version", paths)
        self.assertIn("/v1/state/build", paths)
        self.assertIn("/v1/state/contracts", paths)
        self.assertIn("/v1/state/config-effective", paths)
        self.assertIn("/v1/state/dbus-diagnostics", paths)
        self.assertEqual(
            paths["/v1/capabilities"]["get"]["security"],
            [{"BearerAuth": []}],
        )
        self.assertNotIn("security", paths["/v1/state/healthz"]["get"])
        self.assertIn("requestBody", paths["/v1/control/command"]["post"])
        self.assertIn("429", paths["/v1/control/command"]["post"]["responses"])
        command_parameter_names = [parameter["name"] for parameter in paths["/v1/control/command"]["post"]["parameters"]]
        self.assertIn("If-Match", command_parameter_names)
        self.assertIn("X-State-Token", command_parameter_names)
        self.assertEqual(
            sorted(paths["/v1/capabilities"]["get"]["responses"]["200"]["headers"]),
            ["ETag", "X-State-Token"],
        )
        self.assertEqual(
            paths["/v1/events"]["get"]["responses"]["200"]["content"]["application/x-ndjson"]["schema"]["$ref"],
            "#/components/schemas/ControlEvent",
        )
        event_parameter_names = [parameter["name"] for parameter in paths["/v1/events"]["get"]["parameters"]]
        self.assertIn("kind", event_parameter_names)
        self.assertIn("resume", event_parameter_names)
        self.assertIn("heartbeat", event_parameter_names)
        self.assertEqual(
            list(paths["/v1/events"]["get"]["responses"]["200"]["headers"]),
            ["X-Control-Api-Retry-Ms"],
        )

        schemas = spec["components"]["schemas"]
        self.assertIn("ControlCapabilities", schemas)
        self.assertIn("ControlEvent", schemas)
        self.assertIn("ControlCommandResponse", schemas)
        self.assertIn("ControlCommandRequest", schemas)
        self.assertIn("SetModeCommandRequest", schemas)
        self.assertIn("SetAutoRuntimeFloatCommandRequest", schemas)
        self.assertIn("ControlVersioning", schemas)
        self.assertIn("StateHealthz", schemas)
        self.assertIn("StateVersion", schemas)
        self.assertIn("StateBuild", schemas)
        self.assertIn("StateContracts", schemas)
        self.assertIn("StateConfigEffective", schemas)
        self.assertIn("StateHealth", schemas)
        self.assertIn("StateDbusDiagnostics", schemas)
        self.assertIn("/v1/openapi.json", schemas["ControlCapabilities"]["properties"]["endpoints"]["items"]["enum"])
        self.assertEqual(
            schemas["ControlCapabilities"]["properties"]["versioning"]["$ref"],
            "#/components/schemas/ControlVersioning",
        )
        self.assertIn(
            "/v1/events",
            schemas["ControlVersioning"]["properties"]["experimental_endpoints"]["items"]["enum"],
        )
        self.assertEqual(
            schemas["ControlCommandRequest"]["oneOf"][0]["$ref"],
            "#/components/schemas/LegacyUnknownWriteCommandRequest",
        )
        self.assertEqual(
            schemas["SetModeCommandRequest"]["properties"]["value"]["enum"],
            [0, 1, 2],
        )
        self.assertIn(
            "/Auto/ScheduledLatestEndTime",
            schemas["SetAutoRuntimeStringCommandRequest"]["properties"]["path"]["enum"],
        )

        error_schema = schemas["ControlError"]
        self.assertIn("unauthorized", error_schema["properties"]["code"]["enum"])
        self.assertIn("idempotency_conflict", error_schema["properties"]["code"]["enum"])
        self.assertIn("rate_limited", error_schema["properties"]["code"]["enum"])
        self.assertIn("validation_error", error_schema["properties"]["code"]["enum"])
        self.assertEqual(
            spec["components"]["securitySchemes"],
            {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "opaque-token",
                }
            },
        )

    def test_openapi_projection_matches_golden_snapshot(self) -> None:
        spec = build_control_api_openapi_spec()
        projection = {
            "paths": sorted(spec["paths"]),
            "request_schema_refs": sorted(
                item["$ref"].split("/")[-1] for item in spec["components"]["schemas"]["ControlCommandRequest"]["oneOf"]
            ),
            "error_codes": spec["components"]["schemas"]["ControlError"]["properties"]["code"]["enum"],
            "command_header_parameters": [
                parameter["name"] for parameter in spec["paths"]["/v1/control/command"]["post"]["parameters"]
            ],
            "event_parameters": [parameter["name"] for parameter in spec["paths"]["/v1/events"]["get"]["parameters"]],
            "event_response_headers": list(spec["paths"]["/v1/events"]["get"]["responses"]["200"]["headers"]),
        }
        golden = {
            "paths": [
                "/v1/capabilities",
                "/v1/control/command",
                "/v1/control/health",
                "/v1/events",
                "/v1/openapi.json",
                "/v1/state/automation",
                "/v1/state/build",
                "/v1/state/config-effective",
                "/v1/state/contracts",
                "/v1/state/dbus-diagnostics",
                "/v1/state/health",
                "/v1/state/healthz",
                "/v1/state/operational",
                "/v1/state/runtime",
                "/v1/state/summary",
                "/v1/state/topology",
                "/v1/state/update",
                "/v1/state/version",
                "/v1/state/victron-bias-recommendation",
            ],
            "request_schema_refs": [
                "LegacyUnknownWriteCommandRequest",
                "ResetContactorLockoutCommandRequest",
                "ResetContactorLockoutPathRequest",
                "ResetPhaseLockoutCommandRequest",
                "ResetPhaseLockoutPathRequest",
                "SetAutoRuntimeBinaryCommandRequest",
                "SetAutoRuntimeBinaryPathRequest",
                "SetAutoRuntimeFloatCommandRequest",
                "SetAutoRuntimeFloatPathRequest",
                "SetAutoRuntimeIntegerCommandRequest",
                "SetAutoRuntimeIntegerPathRequest",
                "SetAutoRuntimeStringCommandRequest",
                "SetAutoRuntimeStringPathRequest",
                "SetAutoStartCommandRequest",
                "SetAutoStartPathRequest",
                "SetCurrentSettingCommandRequest",
                "SetCurrentSettingPathRequest",
                "SetEnableCommandRequest",
                "SetEnablePathRequest",
                "SetModeCommandRequest",
                "SetModePathRequest",
                "SetPhaseSelectionCommandRequest",
                "SetPhaseSelectionPathRequest",
                "SetStartStopCommandRequest",
                "SetStartStopPathRequest",
                "TriggerSoftwareUpdateCommandRequest",
                "TriggerSoftwareUpdatePathRequest",
            ],
            "error_codes": [
                "bad_request",
                "blocked_by_health",
                "blocked_by_mode",
                "command_rejected",
                "conflict",
                "cooldown_active",
                "forbidden_remote_client",
                "idempotency_conflict",
                "insufficient_scope",
                "invalid_content_length",
                "invalid_json",
                "invalid_payload",
                "not_found",
                "rate_limited",
                "unauthorized",
                "unsupported_command",
                "unsupported_for_topology",
                "update_in_progress",
                "validation_error",
            ],
            "command_header_parameters": ["Idempotency-Key", "X-Command-Id", "If-Match", "X-State-Token"],
            "event_parameters": ["limit", "after", "resume", "timeout", "heartbeat", "kind", "once"],
            "event_response_headers": ["X-Control-Api-Retry-Ms"],
        }
        self.assertEqual(json.dumps(projection, sort_keys=True), json.dumps(golden, sort_keys=True))
