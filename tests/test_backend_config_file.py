# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
from pathlib import Path
from types import SimpleNamespace
import unittest

from venus_evcharger.backend.config_file import (
    backend_request_timeout_seconds,
    config_section,
    fixed_supported_phase_selections,
    load_required_backend_config,
    normalized_optional_lower_text,
    normalized_optional_path,
    section_is_effectively_empty,
    validate_fixed_phase_selection,
)


class BackendConfigFileTests(unittest.TestCase):
    def test_optional_value_normalizers_trim_empty_and_keep_payload(self) -> None:
        self.assertIsNone(normalized_optional_path(None))
        self.assertIsNone(normalized_optional_path("   "))
        self.assertEqual(normalized_optional_path(" /data/backend.ini "), Path("/data/backend.ini"))
        self.assertIsNone(normalized_optional_lower_text(None))
        self.assertIsNone(normalized_optional_lower_text(" \t "))
        self.assertEqual(normalized_optional_lower_text(" Shelly_Meter "), "shelly_meter")

    def test_load_required_backend_config_strips_path_and_fails_fast(self) -> None:
        with self.subTest("load trimmed path"):
            with self.temporary_config("[Adapter]\nType=template_meter\n") as path:
                parser = load_required_backend_config(f" {path} ", "Meter")
                self.assertEqual(parser["Adapter"]["Type"], "template_meter")
        with self.subTest("missing file"):
            with self.assertRaises(FileNotFoundError) as raised:
                load_required_backend_config(" /missing/backend.ini ", "Meter")
            self.assertEqual(str(raised.exception), "Meter config not found:  /missing/backend.ini ")

    def test_config_section_and_empty_section_contract(self) -> None:
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = {"Type": "template_meter"}
        parser["Adapter"] = {"Type": "shelly_meter"}
        parser["Empty"] = {}

        self.assertIs(config_section(parser, "Adapter"), parser["Adapter"])
        self.assertIs(config_section(parser, "Missing"), parser["DEFAULT"])
        self.assertFalse(section_is_effectively_empty(parser["DEFAULT"]))
        self.assertFalse(section_is_effectively_empty(parser["Adapter"]))
        self.assertFalse(section_is_effectively_empty(parser["Empty"]))

        empty_parser = configparser.ConfigParser()
        empty_parser["Empty"] = {}
        empty_default = empty_parser["DEFAULT"]
        self.assertTrue(section_is_effectively_empty(empty_default))
        self.assertTrue(section_is_effectively_empty(empty_parser["Empty"]))

    def test_backend_request_timeout_prefers_positive_adapter_value(self) -> None:
        service = SimpleNamespace(shelly_request_timeout_seconds=4.0)

        positive = backend_request_timeout_seconds({"RequestTimeoutSeconds": "1.5"}, service)
        zero = backend_request_timeout_seconds({"RequestTimeoutSeconds": "0"}, service)
        negative = backend_request_timeout_seconds({"RequestTimeoutSeconds": "-1"}, service)
        invalid = backend_request_timeout_seconds({"RequestTimeoutSeconds": "bad"}, service)
        service_default = backend_request_timeout_seconds({}, service)
        missing_service_default = backend_request_timeout_seconds({}, SimpleNamespace())
        one_second = backend_request_timeout_seconds({"RequestTimeoutSeconds": "1.0"}, service)
        invalid_service_default = backend_request_timeout_seconds(
            {},
            SimpleNamespace(shelly_request_timeout_seconds="bad"),
            default=3.0,
        )
        negative_service_default = backend_request_timeout_seconds(
            {},
            SimpleNamespace(shelly_request_timeout_seconds="-1"),
            default=3.0,
        )

        self.assertEqual(positive, 1.5)
        self.assertIs(type(positive), float)
        self.assertEqual(zero, 2.0)
        self.assertIs(type(zero), float)
        self.assertEqual(negative, 2.0)
        self.assertEqual(invalid, 2.0)
        self.assertEqual(service_default, 4.0)
        self.assertIs(type(service_default), float)
        self.assertEqual(missing_service_default, 2.0)
        self.assertEqual(one_second, 1.0)
        self.assertEqual(invalid_service_default, 3.0)
        self.assertEqual(negative_service_default, 3.0)

    def test_fixed_supported_phase_selections_require_single_fixed_value(self) -> None:
        parser = configparser.ConfigParser()
        self.assertEqual(fixed_supported_phase_selections(parser, ("P1_P2",), "Demo"), ("P1_P2",))

        parser["Capabilities"] = {}
        self.assertEqual(fixed_supported_phase_selections(parser, ("P1_P2",), "Demo"), ("P1_P2",))

        parser["Capabilities"] = {"SupportedPhaseSelections": "P1_P2"}
        self.assertEqual(fixed_supported_phase_selections(parser, ("P1",), "Demo"), ("P1_P2",))

        case_sensitive = configparser.ConfigParser()
        case_sensitive.optionxform = str
        case_sensitive["Capabilities"] = {"SupportedPhaseSelections": "P1_P2_P3"}
        self.assertEqual(fixed_supported_phase_selections(case_sensitive, ("P1",), "Demo"), ("P1_P2_P3",))

        parser["Capabilities"] = {"SupportedPhaseSelections": "P1,P1_P2"}
        with self.assertRaises(ValueError) as raised:
            fixed_supported_phase_selections(parser, ("P1",), "Demo")
        self.assertEqual(
            str(raised.exception),
            "Demo charger backend requires exactly one fixed [Capabilities] SupportedPhaseSelections value",
        )

    def test_validate_fixed_phase_selection_accepts_aliases_and_rejects_mismatch(self) -> None:
        validate_fixed_phase_selection("L1", "P1", "Demo")
        validate_fixed_phase_selection("unknown", "P1_P2", "Demo")

        with self.assertRaises(ValueError) as raised:
            validate_fixed_phase_selection("P1_P2_P3", "P1_P2", "Demo")
        self.assertEqual(
            str(raised.exception),
            "Demo charger backend does not support native phase switching "
            "(configured fixed phase selection: P1_P2)",
        )

    @staticmethod
    def temporary_config(content: str) -> "_TemporaryConfig":
        return _TemporaryConfig(content)


class _TemporaryConfig:
    def __init__(self, content: str) -> None:
        self.content = content
        self.path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        with handle:
            handle.write(self.content)
        self.path = Path(handle.name)
        return self.path

    def __exit__(self, *_exc: object) -> None:
        if self.path is not None:
            self.path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
