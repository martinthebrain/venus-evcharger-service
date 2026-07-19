# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and composition contracts for the state-controller facade."""

from __future__ import annotations

import configparser
import os
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.state_config import StateConfigLoader
from venus_evcharger.controllers.state_contracts import StateControllerComponents


def _normalize_mode(value: object) -> int:
    if isinstance(value, (bool, int, float, str)):
        return int(value)
    raise TypeError("mode must be a scalar value")


@dataclass
class OverrideRecorder:
    applied: configparser.ConfigParser | None = None

    def apply_to_config(self, config: configparser.ConfigParser) -> configparser.ConfigParser:
        self.applied = config
        config["DEFAULT"]["Applied"] = "1"
        return config

    def current(self) -> dict[str, str]:
        return {}

    def save(self) -> None:
        return None

    def flush(self, now: float | None = None) -> None:
        return None


class TestStateControllerConfigContracts(unittest.TestCase):
    def test_constructor_exposes_immutable_components_and_exact_default_path(self) -> None:
        controller = ServiceStateController(SimpleNamespace(), _normalize_mode)
        self.assertIsInstance(controller.components, StateControllerComponents)
        expected = Path(__file__).resolve().parents[1] / "deploy" / "venus" / "config.venus_evcharger.ini"
        self.assertEqual(Path(controller.config_path()), expected)
        self.assertTrue(os.path.isabs(controller.config_path()))

    def test_config_component_reads_required_host_and_applies_override_port(self) -> None:
        recorder = OverrideRecorder()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[DEFAULT]\nHost=192.0.2.7\n", encoding="utf-8")
            result = StateConfigLoader(recorder, lambda: str(path)).load()
        self.assertIs(result, recorder.applied)
        self.assertEqual(result["DEFAULT"]["Host"], "192.0.2.7")
        self.assertEqual(result["DEFAULT"]["Applied"], "1")

    def test_config_component_rejects_missing_file_and_host_with_exact_error(self) -> None:
        expected = (
            "deploy/venus/config.venus_evcharger.ini is missing or incomplete. "
            "Copy it from the documented deploy/venus/config.venus_evcharger.ini template so the required keys exist."
        )
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.ini"
            no_host = Path(directory) / "no-host.ini"
            no_host.write_text("[DEFAULT]\nOther=value\n", encoding="utf-8")
            for path in (missing, no_host):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError) as raised:
                        StateConfigLoader(OverrideRecorder(), lambda: str(path)).load()
                self.assertEqual(str(raised.exception), expected)

    def test_runtime_read_facade_delegates_to_its_snapshot_and_override_ports(self) -> None:
        controller = ServiceStateController(SimpleNamespace(), _normalize_mode)
        snapshot = {"mode": 2}
        overrides = {"Mode": "2"}
        with (
            patch.object(controller.components.snapshot, "build", return_value=snapshot) as build,
            patch.object(controller.components.overrides, "current", return_value=overrides) as current,
        ):
            self.assertIs(controller.current_runtime_state(), snapshot)
            self.assertIs(controller.current_runtime_overrides(), overrides)
        build.assert_called_once_with()
        current.assert_called_once_with()
