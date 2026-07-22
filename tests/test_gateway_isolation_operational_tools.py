# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import ast
import importlib
import json
import shlex
import sys
import unittest
from pathlib import Path

DEV_SCRIPTS = Path("scripts/dev").resolve()


class _PiSession:
    def __init__(self, service: str, values: dict[str, object]) -> None:
        self.service = service
        self.values = values
        self.commands: list[str] = []
        self._write_count = 0

    def ssh(self, script: str, *, timeout: float = 30.0) -> str:
        del timeout
        self.commands.append(script)
        if script.startswith("cat "):
            return json.dumps(
                {
                    "captured_at": 1.0,
                    "values": {
                        f"path:{self.service}{path}": {
                            "value": value,
                            "status": "fresh",
                        }
                        for path, value in self.values.items()
                    },
                }
            )
        self._write_count += 1
        self.values["/Mode"] = 1 if self._write_count == 1 else 2
        return '{"ok":true}'


class GatewayIsolationOperationalToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(DEV_SCRIPTS))
        cls.assertions = importlib.import_module("pi_gateway_release_gate_assertions")
        cls.isolation = importlib.import_module("check_dbus_isolation")
        cls.remote = importlib.import_module("pi_gateway_release_gate_remote")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(DEV_SCRIPTS))

    def test_release_gate_reads_gui_values_from_gateway_cache(self) -> None:
        service = "com.victronenergy.evcharger.http_60"
        values = {
            "/Connected": 1,
            "/Mode": 2,
            "/StartStop": 1,
            "/AutoStart": 1,
            "/SetCurrent": 10,
            "/Ac/Power": 2000,
            "/Ac/Current": 8.7,
            "/Session/Energy": 0.5,
            "/Session/Time": 10,
            "/ChargingTime": 10,
        }
        pi = _PiSession(service, values)

        numeric = self.assertions.assert_gui_values(pi, service, "/run/gateway", expect_power=True)

        self.assertEqual(numeric["/Ac/Power"], 2000.0)
        self.assertTrue(all(command.startswith("cat ") for command in pi.commands))

    def test_release_gate_write_uses_semantic_core_mailbox_and_restores_mode(self) -> None:
        service = "com.victronenergy.evcharger.http_60"
        pi = _PiSession(service, {"/Mode": 2})

        self.assertions.exercise_gui_write(pi, service, "/run/gateway", "/data/service")

        write_commands = [command for command in pi.commands if not command.startswith("cat ")]
        self.assertEqual(len(write_commands), 2)
        self.assertTrue(
            all(
                "CoreCommandMailbox" in command
                and "core_control_command_payload" in command
                and "set_value" not in command
                for command in write_commands
            )
        )
        self.assertIn("'set_mode','mode',1", shlex.split(write_commands[0])[-1])
        self.assertIn("'set_mode','mode',2", shlex.split(write_commands[1])[-1])
        self.assertTrue(all(self.isolation.FORBIDDEN_CLI_PATTERN.search(command) is None for command in write_commands))
        self.assertEqual(pi.values["/Mode"], 2)

    def test_release_gate_deploy_excludes_local_development_artifacts(self) -> None:
        excludes = set(self.remote.DEPLOY_EXCLUDES)

        self.assertIn("--exclude=.venv*", excludes)
        self.assertIn("--exclude=.mutmut-cache", excludes)
        self.assertIn("--exclude=.coverage", excludes)
        self.assertIn("--exclude=coverage.xml", excludes)
        self.assertIn("--exclude=__pycache__", excludes)

    def test_isolation_pattern_rejects_supported_direct_clients_only_as_commands(self) -> None:
        forbidden = (
            "dbus -y service /Path GetValue",
            "dbus-send --system --dest=service /Path",
            "gdbus call --system --dest service",
            "busctl get-property service /Path interface Value",
            "dbus-monitor --system",
        )
        self.assertTrue(all(self.isolation.FORBIDDEN_CLI_PATTERN.search(command) for command in forbidden))
        self.assertIsNone(self.isolation.FORBIDDEN_CLI_PATTERN.search("DBus gateway health"))

        visitor = self.isolation.DbusIsolationVisitor(Path(self.isolation.__file__))
        visitor.visit(ast.parse('subprocess.run(["/usr/bin/dbus", "-y", "service"])\nshutil.which("gdbus")'))
        self.assertEqual(len(visitor.violations), 2)


if __name__ == "__main__":
    unittest.main()
