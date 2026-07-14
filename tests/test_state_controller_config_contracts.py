# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused configuration contracts for the state controller facade."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.controllers.state import ServiceStateController


def _normalize_mode(value: object) -> int:
    return int(value)


class TestStateControllerConfigContracts(unittest.TestCase):
    def test_constructor_and_default_config_path_are_exact(self) -> None:
        service = SimpleNamespace()
        controller = ServiceStateController(service, _normalize_mode)
        self.assertIs(controller.service, service)
        self.assertIs(controller._normalize_mode, _normalize_mode)
        expected = Path(__file__).resolve().parents[1] / "deploy" / "venus" / "config.venus_evcharger.ini"
        self.assertEqual(Path(controller.config_path()), expected)
        self.assertTrue(os.path.isabs(controller.config_path()))

    def test_load_config_reads_required_host_and_applies_runtime_overrides(self) -> None:
        service = SimpleNamespace()
        controller = ServiceStateController(service, _normalize_mode)
        sentinel = MagicMock()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[DEFAULT]\nHost=192.0.2.7\n", encoding="utf-8")
            with (
                patch.object(controller, "config_path", return_value=str(path)) as config_path,
                patch.object(controller, "_apply_runtime_overrides_to_config", return_value=sentinel) as apply,
            ):
                result = controller.load_config()
        self.assertIs(result, sentinel)
        config_path.assert_called_once_with()
        apply.assert_called_once()
        self.assertIs(apply.call_args.args[0], service)
        self.assertEqual(apply.call_args.args[1]["DEFAULT"]["Host"], "192.0.2.7")

    def test_load_config_rejects_missing_file_section_and_host_with_exact_error(self) -> None:
        expected = (
            "deploy/venus/config.venus_evcharger.ini is missing or incomplete. "
            "Copy it from the documented deploy/venus/config.venus_evcharger.ini template so the required keys exist."
        )
        controller = ServiceStateController(SimpleNamespace(), _normalize_mode)
        with TemporaryDirectory() as directory:
            paths = []
            missing = Path(directory) / "missing.ini"
            paths.append(missing)
            no_host = Path(directory) / "no-host.ini"
            no_host.write_text("[DEFAULT]\nOther=value\n", encoding="utf-8")
            paths.append(no_host)
            for path in paths:
                with self.subTest(path=path), patch.object(controller, "config_path", return_value=str(path)):
                    with self.assertRaises(ValueError) as raised:
                        controller.load_config()
                self.assertEqual(str(raised.exception), expected)


if __name__ == "__main__":
    unittest.main()
