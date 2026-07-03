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
        with patch("venus_evcharger.control.openapi.build_control_api_openapi_spec", return_value={}):
            with self.assertRaisesRegex(TypeError, "must contain components mapping"):
                control_reference._control_api_component_schemas()
        with patch(
            "venus_evcharger.control.openapi.build_control_api_openapi_spec",
            return_value={"components": {"schemas": []}},
        ):
            with self.assertRaisesRegex(TypeError, "components must contain schemas mapping"):
                control_reference._control_api_component_schemas()

        self.assertIsNone(control_reference._named_schema_command_name(object()))
        self.assertIsNone(control_reference._named_schema_command_name({"properties": []}))
        self.assertIsNone(control_reference._named_schema_command_name({"properties": {"name": []}}))
        self.assertIsNone(control_reference._named_schema_command_name({"properties": {"name": {"const": 1}}}))
        self.assertEqual(
            control_reference._named_schema_command_name({"properties": {"name": {"const": "set_mode"}}}),
            "set_mode",
        )

        self.assertEqual(control_reference._format_scalar(True), "`1`")
        self.assertEqual(control_reference._format_scalar(1.5), "`1.5`")
        self.assertEqual(control_reference._const_label({"const": "value"}), "`value`")
        self.assertEqual(control_reference._schema_allowed_values({"const": "value"}), "`value`")
        self.assertEqual(control_reference._schema_allowed_values({"type": "string"}), "implementation-defined")
        self.assertEqual(control_reference._joined_labels({"integer", "string"}, path_specific=False), "integer or string")

        self.assertEqual(
            control_reference._collected_required_fields(
                [
                    {"required": ["name", "value"]},
                    {"required": "invalid"},
                ]
            ),
            {"name", "value"},
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


if __name__ == "__main__":
    unittest.main()
