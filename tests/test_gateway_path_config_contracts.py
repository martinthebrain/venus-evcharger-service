# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-process contracts for the canonical gateway IPC paths."""

from __future__ import annotations

import configparser
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast
from unittest.mock import patch

import venus_evcharger.inputs.helper.energy_gateway as energy_gateway_module
import venus_evcharger_generic_shelly_configuration as generic_shelly_entrypoint
from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs
from venus_evcharger.bootstrap.config_auto_helper_gateway import (
    load_gateway_path_config,
)
from venus_evcharger.dbus_adapter.process.config import adapter_settings
from venus_evcharger.dbus_gateway_core import GatewayPaths, gateway_paths
from venus_evcharger.inputs.helper.config_runtime import (
    load_auto_input_helper_settings,
)
from venus_evcharger.inputs.helper.energy_gateway import GatewayEnergySnapshots
from venus_evcharger.ipc.gateway_path_config import (
    configured_gateway_paths,
    load_configured_gateway_paths,
)
from venus_evcharger.service.controller_owner import ServiceControllerOwner


class _AdapterEntrypoint(Protocol):
    DbusAdapter: type[object]

    def main(self, argv: list[str] | None = None) -> int: ...


install_venus_adapter_stubs()
adapter_entrypoint = cast(
    _AdapterEntrypoint,
    importlib.import_module("venus_evcharger_dbus_adapter"),
)


def _config_text(root: Path) -> str:
    return f"""[DEFAULT]
DbusGatewayRunDir = {root}/run
DbusGatewayCachePath = {root}/cache/custom-cache.json
DbusGatewayHealthPath = {root}/health/custom-health.json
DbusGatewaySocketPath = {root}/socket/custom.sock
DbusGatewayCommandDir = {root}/queues/gateway
DbusGatewayCoreCommandDir = {root}/queues/core
"""


def _expected_paths(root: Path) -> GatewayPaths:
    return GatewayPaths(
        run_dir=str(root / "run"),
        socket_path=str(root / "socket/custom.sock"),
        cache_path=str(root / "cache/custom-cache.json"),
        cache_sequence_path=str(root / "run/dbus-cache.seq"),
        health_path=str(root / "health/custom-health.json"),
        energy_inputs_path=str(root / "run/energy-inputs.v2.bin"),
        energy_topology_path=str(root / "run/energy-topology.json"),
        command_dir=str(root / "queues/gateway"),
        core_command_dir=str(root / "queues/core"),
    )


class GatewayPathConfigContracts(unittest.TestCase):
    def test_parser_section_and_case_insensitive_mapping_are_identical(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(_config_text(Path("/tmp/gateway-contract")))
        expected = _expected_paths(Path("/tmp/gateway-contract"))
        lowercase_mapping = {key.lower(): value for key, value in parser["DEFAULT"].items()}

        self.assertEqual(configured_gateway_paths(parser), expected)
        self.assertEqual(configured_gateway_paths(parser["DEFAULT"]), expected)
        self.assertEqual(configured_gateway_paths(lowercase_mapping), expected)

    def test_explicit_run_dir_replaces_every_configured_gateway_path(self) -> None:
        configured = configured_gateway_paths(
            {
                "DbusGatewayRunDir": "/tmp/configured-run",
                "DbusGatewayCachePath": " /tmp/explicit-cache.json ",
                "DbusGatewayHealthPath": "/tmp/explicit-health.json",
                "DbusGatewaySocketPath": "/tmp/explicit.sock",
                "DbusGatewayCommandDir": "/tmp/explicit-commands",
                "DbusGatewayCoreCommandDir": "/tmp/explicit-core-commands",
            },
            run_dir_override=" /tmp/command-line-run ",
        )

        self.assertEqual(configured, gateway_paths("/tmp/command-line-run"))

    def test_explicit_run_dir_ignores_invalid_configured_paths(self) -> None:
        configured = configured_gateway_paths(
            {
                "DbusGatewayRunDir": "relative/configured-run",
                "DbusGatewayCachePath": "relative/cache.json",
                "DbusGatewayHealthPath": "relative/health.json",
                "DbusGatewaySocketPath": "relative/socket",
                "DbusGatewayCommandDir": "relative/commands",
                "DbusGatewayCoreCommandDir": "relative/core-commands",
            },
            run_dir_override="/tmp/isolated-gateway",
        )

        self.assertEqual(configured, gateway_paths("/tmp/isolated-gateway"))

    def test_explicit_run_dir_must_be_nonempty_and_absolute(self) -> None:
        for override, displayed_value in (
            ("relative/runtime", "relative/runtime"),
            (" ", ""),
        ):
            with (
                self.subTest(override=override),
                self.assertRaisesRegex(
                    ValueError,
                    rf"DbusGatewayRunDir must be an absolute path: {displayed_value}$",
                ),
            ):
                configured_gateway_paths(
                    {"DbusGatewayRunDir": "/tmp/configured-run"},
                    run_dir_override=override,
                )

    def test_default_and_environment_run_directories_remain_supported(self) -> None:
        self.assertEqual(configured_gateway_paths({}), gateway_paths())
        with patch.dict(
            os.environ,
            {"VENUS_EVCHARGER_GATEWAY_RUN_DIR": "/tmp/environment-gateway"},
        ):
            self.assertEqual(
                configured_gateway_paths({}),
                gateway_paths("/tmp/environment-gateway"),
            )
        with (
            patch.dict(
                os.environ,
                {"VENUS_EVCHARGER_GATEWAY_RUN_DIR": "relative/environment-gateway"},
            ),
            self.assertRaisesRegex(
                ValueError,
                "DbusGatewayRunDir must be an absolute path",
            ),
        ):
            configured_gateway_paths({})

    def test_loader_is_fail_fast_and_every_configured_path_must_be_absolute(self) -> None:
        missing = "/tmp/missing-gateway-path-config.ini"
        with self.assertRaisesRegex(ValueError, f"Unable to read config file: {missing}"):
            load_configured_gateway_paths(missing)

        keys = (
            "DbusGatewayRunDir",
            "DbusGatewayCachePath",
            "DbusGatewayHealthPath",
            "DbusGatewaySocketPath",
            "DbusGatewayCommandDir",
            "DbusGatewayCoreCommandDir",
        )
        for key in keys:
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(
                    ValueError,
                    rf"{key} must be an absolute path",
                ),
            ):
                configured_gateway_paths({key: "relative/path"})

    def test_loader_forwards_run_dir_override_as_complete_path_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.ini"
            config_path.write_text(_config_text(root), encoding="utf-8")
            cli_run_dir = root / "cli-run"

            paths = load_configured_gateway_paths(
                str(config_path),
                run_dir_override=str(cli_run_dir),
            )

        self.assertEqual(paths, gateway_paths(str(cli_run_dir)))

    def test_adapter_cli_passes_one_coherent_override_path_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.ini"
            config_path.write_text(_config_text(root), encoding="utf-8")
            cli_run_dir = root / "cli-run"

            with patch.object(adapter_entrypoint, "DbusAdapter") as adapter_type:
                result = adapter_entrypoint.main([str(config_path), "--run-dir", str(cli_run_dir)])

        self.assertEqual(result, 0)
        adapter_type.assert_called_once_with(
            str(config_path),
            paths=gateway_paths(str(cli_run_dir)),
        )
        adapter_type.return_value.run.assert_called_once_with()

    def test_adapter_core_auto_helper_and_generic_helper_share_exact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.ini"
            config_path.write_text(_config_text(root), encoding="utf-8")
            parser = configparser.ConfigParser()
            parser.read(config_path)
            expected = _expected_paths(root)

            adapter_paths = adapter_settings(parser["DEFAULT"]).paths

            service = SimpleNamespace()
            load_gateway_path_config(service, parser["DEFAULT"])
            owner = object.__new__(ServiceControllerOwner)
            owner._service = service
            core_paths = owner._resolve_gateway_paths()
            owner._gateway_paths = None
            with self.assertRaisesRegex(
                RuntimeError,
                "gateway paths are not initialized",
            ):
                owner._required_gateway_paths()
            owner._gateway_paths = core_paths
            owner._apply_gateway_paths(core_paths)
            self.assertEqual(owner._required_gateway_paths(), expected)

            helper_settings = load_auto_input_helper_settings(
                str(config_path),
                None,
                None,
                None,
                "path-contract",
            )
            with patch.object(
                energy_gateway_module,
                "GatewayClient",
            ) as helper_client:
                GatewayEnergySnapshots(helper_settings)
            helper_client.assert_called_once_with(expected)

            with (
                patch.object(
                    generic_shelly_entrypoint,
                    "GatewayClient",
                ) as generic_client,
                patch.object(
                    generic_shelly_entrypoint,
                    "configuration_main",
                    return_value=0,
                ) as workflow,
            ):
                result = generic_shelly_entrypoint.main((str(config_path),))
            generic_client.assert_called_once_with(expected)
            workflow.assert_called_once()

        self.assertEqual(adapter_paths, expected)
        self.assertEqual(core_paths, expected)
        self.assertEqual(result, 0)

    def test_generic_helper_rejects_an_unreadable_path_config_before_composition(self) -> None:
        with (
            patch.object(
                generic_shelly_entrypoint,
                "GatewayClient",
            ) as gateway_client,
            patch.object(
                generic_shelly_entrypoint,
                "configuration_main",
            ) as workflow,
        ):
            result = generic_shelly_entrypoint.main(("/missing/config.ini",))

        self.assertEqual(result, 1)
        gateway_client.assert_not_called()
        workflow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
