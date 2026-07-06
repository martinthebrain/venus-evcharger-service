# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest
from unittest.mock import patch

import venus_evcharger.control.reference as control_reference
from venus_evcharger.control import (
    CONTROL_API_COMMAND_REFERENCE,
    CONTROL_API_COMMAND_SCOPE_REQUIREMENTS,
    build_control_api_openapi_spec,
    render_control_api_command_matrix_markdown,
)


class TestVenusEvchargerControlReference(unittest.TestCase):
    def test_rendered_command_matrix_matches_documented_block(self) -> None:
        document = pathlib.Path("CONTROL_API.md").read_text(encoding="utf-8")
        begin_marker = "<!-- BEGIN:CONTROL_API_COMMAND_MATRIX -->"
        end_marker = "<!-- END:CONTROL_API_COMMAND_MATRIX -->"

        begin = document.index(begin_marker) + len(begin_marker)
        end = document.index(end_marker)
        documented_block = document[begin:end].strip()

        self.assertEqual(documented_block, render_control_api_command_matrix_markdown())

    def test_reference_scopes_match_shared_scope_contract(self) -> None:
        self.assertEqual(
            {item.name for item in CONTROL_API_COMMAND_REFERENCE},
            set(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
        )
        self.assertEqual(
            len(CONTROL_API_COMMAND_REFERENCE),
            len(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
        )

    def test_reference_required_fields_match_named_openapi_request_schemas(self) -> None:
        spec = build_control_api_openapi_spec()
        schemas = spec["components"]["schemas"]

        for item in CONTROL_API_COMMAND_REFERENCE:
            matching_named_schemas = []
            for schema_name, schema in schemas.items():
                properties = schema.get("properties", {})
                name_property = properties.get("name", {})
                if name_property.get("const") != item.name:
                    continue
                matching_named_schemas.append(schema)

            self.assertTrue(matching_named_schemas, msg=f"Missing named request schema for {item.name}")
            self.assertTrue(
                any("value" in schema.get("required", ()) for schema in matching_named_schemas),
                msg=f"Schema for {item.name} should require a value field.",
            )

    def test_reference_command_names_match_openapi_capabilities_enum(self) -> None:
        spec = build_control_api_openapi_spec()
        capability_names = (
            spec["components"]["schemas"]["ControlCapabilities"]["properties"]["command_names"]["items"]["enum"]
        )

        self.assertEqual(
            sorted(item.name for item in CONTROL_API_COMMAND_REFERENCE),
            sorted(capability_names),
        )

    def test_private_reference_helpers_cover_remaining_contract_branches(self) -> None:
        schemas = {"Named": {"properties": {"name": {"const": "set_mode"}}}}
        with patch(
            "venus_evcharger.control.openapi.build_control_api_openapi_spec",
            return_value={"components": {"schemas": schemas}},
        ):
            self.assertIs(control_reference._control_api_component_schemas(), schemas)

        with patch("venus_evcharger.control.openapi.build_control_api_openapi_spec", return_value={}):
            with self.assertRaises(TypeError) as error:
                control_reference._control_api_component_schemas()
            self.assertEqual(str(error.exception), "Control API OpenAPI spec must contain components mapping")
        with patch(
            "venus_evcharger.control.openapi.build_control_api_openapi_spec",
            return_value={"components": {"schemas": []}},
        ):
            with self.assertRaises(TypeError) as error:
                control_reference._control_api_component_schemas()
            self.assertEqual(str(error.exception), "Control API OpenAPI components must contain schemas mapping")

        custom_schemas = {
            "Bogus": object(),
            "WithoutName": {"properties": {"value": {"type": "integer"}}},
            "First": {"properties": {"name": {"const": "set_mode"}, "value": {"type": "integer"}}},
            "Second": {"properties": {"name": {"const": "set_mode"}, "value": {"type": "number"}}},
        }
        with patch(
            "venus_evcharger.control.reference._control_api_component_schemas",
            return_value=custom_schemas,
        ):
            self.assertEqual(
                control_reference._named_request_schemas_by_command(),
                {"set_mode": [custom_schemas["First"], custom_schemas["Second"]]},
            )

        self.assertIsNone(control_reference._named_schema_command_name(object()))
        self.assertIsNone(control_reference._named_schema_command_name({"properties": []}))
        self.assertIsNone(control_reference._named_schema_command_name({"properties": {"name": []}}))
        self.assertIsNone(control_reference._named_schema_command_name({"properties": {"name": {"const": 1}}}))
        self.assertEqual(
            control_reference._named_schema_command_name({"properties": {"name": {"const": "set_mode"}}}),
            "set_mode",
        )

        self.assertEqual(control_reference._format_scalar(True), "`1`")
        self.assertEqual(control_reference._format_scalar(False), "`0`")
        self.assertEqual(control_reference._format_scalar("manual"), "`manual`")
        self.assertEqual(control_reference._format_scalar(1.5), "`1.5`")
        self.assertEqual(control_reference._binary_variant_shape({"type": "integer", "enum": [0, 1]}), ("integer", (0, 1)))
        self.assertEqual(control_reference._binary_variant_shape({"type": "integer", "enum": [1, 0]}), ("integer", (1, 0)))
        self.assertEqual(control_reference._binary_variant_shape({"type": "boolean", "enum": "invalid"}), ("boolean", ()))
        self.assertEqual(control_reference._binary_variant_shape({}), (None, ()))
        self.assertEqual(control_reference._enum_label({"enum": ["a", "b"]}), "`a`, `b`")
        self.assertIsNone(control_reference._enum_label({"enum": []}))
        self.assertEqual(control_reference._const_label({"const": "value"}), "`value`")
        self.assertIsNone(control_reference._const_label({"const": ""}))
        self.assertIsNone(control_reference._const_label({"const": None}))
        self.assertEqual(control_reference._schema_allowed_values({"const": "value"}), "`value`")
        self.assertEqual(control_reference._schema_allowed_values({"type": "string"}), "implementation-defined")
        self.assertEqual(control_reference._joined_labels({"string"}, path_specific=True), "string")
        self.assertEqual(control_reference._joined_labels({"integer", "string"}, path_specific=False), "integer or string")
        self.assertEqual(
            control_reference._joined_labels({"string", "integer"}, path_specific=True),
            "integer or string depending on `path`",
        )
        self.assertEqual(
            control_reference._joined_labels({"string", "number", "integer"}, path_specific=True),
            "integer, number, or string depending on `path`",
        )
        self.assertEqual(
            control_reference._joined_labels({"custom", "number", "string"}, path_specific=False),
            "number, string, or custom",
        )
        with patch.dict(control_reference._VALUE_TYPE_ORDER, {"late": 100}):
            self.assertEqual(
                control_reference._joined_labels({"late", "unknown"}, path_specific=False),
                "unknown or late",
            )

        grouped = control_reference._named_request_schemas_by_command()
        self.assertIn("set_mode", grouped)
        self.assertTrue(all(isinstance(schema, dict) for schema in grouped["set_mode"]))

        self.assertEqual(
            control_reference._collected_required_fields(
                [
                    {"required": ["name", "value"]},
                    {"required": ["path", 7]},
                    {"required": []},
                    {"required": "invalid"},
                ]
            ),
            {"name", "value", "path", "7"},
        )
        self.assertEqual(
            control_reference._collected_value_contract_labels(
                [
                    {"properties": []},
                    {"properties": {"value": {"const": "manual"}}},
                ]
            ),
            ({"implementation-defined"}, {"`manual`"}),
        )
        self.assertIsNone(control_reference._schema_value_property({"properties": []}))
        self.assertIsNone(control_reference._schema_value_property({"properties": {"value": []}}))
        self.assertEqual(control_reference._schema_value_property({"properties": {}}), {})
        self.assertEqual(control_reference._schema_value_property({}), {})

    def test_command_contract_summary_handles_single_and_path_specific_shapes(self) -> None:
        with patch(
            "venus_evcharger.control.reference._named_request_schemas_by_command",
            return_value={
                "single": [
                    {
                        "required": ["value", "name"],
                        "properties": {"value": {"type": "integer", "minimum": 6}},
                    }
                ],
                "multi": [
                    {
                        "required": ["value", "name", "path"],
                        "properties": {"value": {"type": "number", "minimum": 0}},
                    },
                    {
                        "required": ["value", "name", "path"],
                        "properties": {"value": {"type": "string", "enum": ["auto", "manual"]}},
                    },
                ],
            },
        ):
            self.assertEqual(
                control_reference._command_contract_summary("single"),
                (("name", "value"), "integer", "`>= 6`"),
            )
            self.assertEqual(
                control_reference._command_contract_summary("multi"),
                (("name", "path", "value"), "number or string depending on `path`", "path-specific schema"),
            )

        with (
            patch(
                "venus_evcharger.control.reference._named_request_schemas_by_command",
                return_value={
                    "single": [
                        {
                            "required": ["value"],
                            "properties": {"value": {"type": "integer"}},
                        }
                    ],
                },
            ),
            patch("venus_evcharger.control.reference._joined_labels", return_value="integer") as joined_labels,
        ):
            self.assertEqual(control_reference._command_contract_summary("single"), (("value",), "integer", "implementation-defined"))
            joined_labels.assert_called_once_with({"integer"}, path_specific=False)


if __name__ == "__main__":
    unittest.main()
