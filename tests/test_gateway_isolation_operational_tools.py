# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import ast
import importlib
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import call, patch

DEV_SCRIPTS = Path("scripts/dev").resolve()


class _PiSession:
    def __init__(self, service: str, values: dict[str, object]) -> None:
        self.service = service
        self.values = values
        self.commands: list[str] = []
        self.timeouts: list[float] = []
        self._write_count = 0
        self.ssh_config = "/dev/null"
        self.target = "root@pi"

    def ssh(self, script: str, *, timeout: float = -1.0) -> str:
        self.commands.append(script)
        self.timeouts.append(timeout)
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


class _FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes | None = b"",
        stderr: bytes | None = b"",
        communicate_stdout: bytes = b"",
        communicate_stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self.stdout = BytesIO(stdout) if stdout is not None else None
        self.stderr = BytesIO(stderr) if stderr is not None else None
        self._communicate_result = (communicate_stdout, communicate_stderr)
        self.communicate_timeouts: list[float | None] = []
        self.wait_timeouts: list[float | None] = []

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        self.communicate_timeouts.append(timeout)
        return self._communicate_result

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        return self.returncode


class GatewayIsolationOperationalToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(DEV_SCRIPTS))
        cls.assertions = importlib.import_module("pi_gateway_release_gate_assertions")
        cls.isolation = importlib.import_module("scripts.dev.check_dbus_isolation")
        cls.deploy = importlib.import_module("scripts.dev.pi_gateway_release_gate_deploy")

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

    def test_release_gate_waits_for_one_consistent_gui_snapshot(self) -> None:
        service = "com.victronenergy.evcharger.http_60"
        pi = _PiSession(
            service,
            {
                "/Connected": 1,
                "/Mode": 0,
                "/StartStop": 1,
                "/AutoStart": 1,
                "/SetCurrent": 10,
                "/Ac/Power": 2000,
                "/Ac/Current": 8.7,
                "/Session/Energy": 0.5,
                "/Session/Time": 10,
                "/ChargingTime": 10,
            },
        )
        with (
            patch.object(
                self.assertions,
                "_assert_power_values",
                side_effect=[self.assertions.GateFailure("not ready"), None],
            ) as validate,
            patch.object(self.assertions.time, "monotonic", side_effect=[0.0, 0.1, 0.2]),
            patch.object(self.assertions.time, "sleep") as sleep,
        ):
            numeric = self.assertions.assert_gui_values(
                pi,
                service,
                "/run/gateway",
                expect_power=True,
                wait_seconds=1.0,
            )

        self.assertEqual(numeric["/Session/Time"], 10.0)
        self.assertEqual(validate.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_release_gate_reports_last_inconsistent_snapshot_at_deadline(self) -> None:
        service = "com.victronenergy.evcharger.http_60"
        pi = _PiSession(
            service,
            {
                "/Connected": 1,
                "/Mode": 0,
                "/StartStop": 0,
                "/AutoStart": 1,
                "/SetCurrent": 10,
                "/Ac/Power": 0,
                "/Ac/Current": 0,
                "/Session/Energy": 0,
                "/Session/Time": 0,
                "/ChargingTime": 0,
            },
        )
        with (
            patch.object(self.assertions.time, "monotonic", side_effect=[0.0, 1.0]),
            self.assertRaisesRegex(self.assertions.GateFailure, "/Ac/Power did not follow simulator"),
        ):
            self.assertions.assert_gui_values(
                pi,
                service,
                "/run/gateway",
                expect_power=True,
                wait_seconds=0.5,
            )

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
        excludes = set(self.deploy.DEPLOY_EXCLUDES)

        self.assertIn("--exclude-vcs-ignores", excludes)
        self.assertIn("--exclude=.venv*", excludes)
        self.assertIn("--exclude=.mutmut-cache", excludes)
        self.assertIn("--exclude=.coverage", excludes)
        self.assertIn("--exclude=coverage.xml", excludes)
        self.assertIn("--exclude=publication-order-state.json.journal", excludes)
        self.assertIn("--exclude=__pycache__", excludes)

    def test_release_gate_activates_exact_tree_with_rollback(self) -> None:
        pi = _PiSession("service", {})

        self.deploy._activate_deployment(
            pi,
            "/data/release gate",
            "/data/release gate.staging",
            "/data/release gate.previous",
        )

        self.assertEqual(
            pi.commands,
            [
                "set -eu; if [ -e '/data/release gate' ]; then mv '/data/release gate' "
                "'/data/release gate.previous'; fi; if mv '/data/release gate.staging' "
                "'/data/release gate'; then rm -rf '/data/release gate.previous'; else if "
                "[ -e '/data/release gate.previous' ]; then mv '/data/release gate.previous' "
                "'/data/release gate'; fi; exit 1; fi"
            ],
        )
        self.assertEqual(pi.timeouts, [30.0])

    def test_release_gate_deploys_to_staging_before_atomic_activation(self) -> None:
        pi = _PiSession("service", {})
        tar_process = _FakeProcess(stdout=b"archive", stderr=b"tar diagnostics")
        ssh_process = _FakeProcess(communicate_stdout=b"remote output")
        tar_stdout = tar_process.stdout
        self.assertIsNotNone(tar_stdout)
        assert tar_stdout is not None

        with patch.object(
            self.deploy.subprocess,
            "Popen",
            side_effect=(tar_process, ssh_process),
        ) as popen:
            self.deploy.deploy_repo(pi, "/data/release gate")

        self.assertEqual(popen.call_count, 2)
        tar_command = popen.call_args_list[0].args[0]
        ssh_command = popen.call_args_list[1].args[0]
        self.assertEqual(
            tar_command,
            ["tar", *self.deploy.DEPLOY_EXCLUDES, "-C", str(self.deploy.ROOT), "-czf", "-", "."],
        )
        self.assertEqual(
            ssh_command,
            [
                "ssh",
                "-F",
                "/dev/null",
                "-o",
                "BatchMode=yes",
                "root@pi",
                "cd '/data/release gate.release-gate-staging' && tar -xzf -",
            ],
        )
        self.assertEqual(
            popen.call_args_list,
            [
                call(
                    tar_command,
                    stdout=self.deploy.subprocess.PIPE,
                    stderr=self.deploy.subprocess.PIPE,
                    text=False,
                ),
                call(
                    ssh_command,
                    stdin=tar_stdout,
                    stdout=self.deploy.subprocess.PIPE,
                    stderr=self.deploy.subprocess.PIPE,
                    text=False,
                ),
            ],
        )
        self.assertIs(popen.call_args_list[1].kwargs["stdin"], tar_stdout)
        self.assertTrue(tar_stdout.closed)
        self.assertEqual(ssh_process.communicate_timeouts, [90.0])
        self.assertEqual(tar_process.wait_timeouts, [10.0])
        self.assertEqual(
            pi.commands,
            [
                "rm -rf '/data/release gate.release-gate-staging' "
                "'/data/release gate.release-gate-previous' && "
                "mkdir -p '/data/release gate.release-gate-staging'",
                "set -eu; if [ -e '/data/release gate' ]; then mv '/data/release gate' "
                "'/data/release gate.release-gate-previous'; fi; if mv "
                "'/data/release gate.release-gate-staging' '/data/release gate'; then rm -rf "
                "'/data/release gate.release-gate-previous'; else if [ -e "
                "'/data/release gate.release-gate-previous' ]; then mv "
                "'/data/release gate.release-gate-previous' '/data/release gate'; fi; exit 1; fi",
            ],
        )
        self.assertEqual(pi.timeouts, [20.0, 30.0])

    def test_release_gate_transfer_failure_preserves_active_tree(self) -> None:
        cases = (
            (1, 0, b"\xfftar failed", b"\xfeout", b"\xfdssh failed"),
            (0, 23, b"\xfftar failed", b"\xfeout", b"\xfdssh failed"),
        )
        for tar_rc, ssh_rc, tar_error, ssh_output, ssh_error in cases:
            with self.subTest(tar_rc=tar_rc, ssh_rc=ssh_rc):
                pi = _PiSession("service", {})
                tar_process = _FakeProcess(returncode=tar_rc, stderr=tar_error)
                ssh_process = _FakeProcess(
                    returncode=ssh_rc,
                    communicate_stdout=ssh_output,
                    communicate_stderr=ssh_error,
                )
                with patch.object(
                    self.deploy.subprocess,
                    "Popen",
                    side_effect=(tar_process, ssh_process),
                ):
                    with self.assertRaises(self.deploy.GateFailure) as caught:
                        self.deploy.deploy_repo(pi, "/data/service")

                self.assertEqual(
                    str(caught.exception),
                    "deploy failed\n"
                    f"tar_rc={tar_rc} tar_stderr=\ufffdtar failed\n"
                    f"ssh_rc={ssh_rc} stdout=\ufffdout stderr=\ufffdssh failed",
                )
                self.assertEqual(
                    pi.commands,
                    [
                        "rm -rf /data/service.release-gate-staging "
                        "/data/service.release-gate-previous && "
                        "mkdir -p /data/service.release-gate-staging",
                        "rm -rf /data/service.release-gate-staging",
                    ],
                )
                self.assertEqual(pi.timeouts, [20.0, 20.0])

    def test_release_gate_rejects_missing_local_archive_pipes(self) -> None:
        cases = (
            (None, b"", "stdout"),
            (b"", None, "stderr"),
        )
        for stdout, stderr, missing_pipe in cases:
            with self.subTest(missing_pipe=missing_pipe):
                pi = _PiSession("service", {})
                tar_process = _FakeProcess(stdout=stdout, stderr=stderr)
                with (
                    patch.object(self.deploy.subprocess, "Popen", return_value=tar_process) as popen,
                    self.assertRaises(self.deploy.GateFailure) as caught,
                ):
                    self.deploy.deploy_repo(pi, "/data/service")

                self.assertEqual(str(caught.exception), f"deploy tar {missing_pipe} pipe unavailable")
                popen.assert_called_once()
                self.assertEqual(
                    pi.commands,
                    [
                        "rm -rf /data/service.release-gate-staging "
                        "/data/service.release-gate-previous && "
                        "mkdir -p /data/service.release-gate-staging",
                        "rm -rf /data/service.release-gate-staging",
                    ],
                )
                self.assertEqual(pi.timeouts, [20.0, 20.0])

    def test_gateway_chaos_script_exercises_current_async_contract(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DEV_SCRIPTS / "dbus_gateway_chaos.py")],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(
            payload.get("results"),
            {
                "competing-grid-sources": "ok",
                "core-overproduction": "ok",
                "dbus-hang": "ok",
                "epoch-clock-jump": "ok",
                "gui-burst": "ok",
                "reboot-mid-burst": "ok",
                "resource-pressure": "ok",
            },
        )

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

    def test_isolation_file_inventory_respects_roots_adapter_ownership_and_cache_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            adapter = root / "gateway.py"
            adapter_package = root / "package" / "dbus_adapter"
            paths = (
                adapter,
                adapter_package / "rate.py",
                adapter_package / "read.py",
                adapter_package / "__pycache__" / "cached.py",
                root / "package" / "core.py",
                root / "package" / "__pycache__" / "cached.py",
                root / "scripts" / "tool.py",
                root / "scripts" / "__pycache__" / "cached.md",
                root / "root.py",
                root / "root.sh",
                root / "README.md",
                root / "private" / "nested.md",
                root / "scripts" / "nested" / "tool.sh",
                root / "scripts" / "notes.md",
                root / "deploy" / "service" / "run",
                root / "deploy" / "notes.md",
                root / "docs" / "design.md",
                root / "examples" / "example.md",
            )
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("pass\n", encoding="utf-8")
            (root / "scripts" / "directory.md").mkdir()

            with patch.multiple(
                self.isolation,
                REPO=root,
                ADAPTER=adapter,
                ADAPTER_PACKAGE=adapter_package,
                ROOT_FILES=("root.py", "missing.py"),
                PRODUCTION_ROOTS=("package", "scripts"),
                SHELL_ROOTS=("scripts", "deploy"),
                DOCUMENTATION_ROOTS=("docs", "examples"),
            ):
                self.assertTrue(self.isolation._adapter_owned(adapter))
                self.assertTrue(self.isolation._adapter_owned(adapter_package / "read.py"))
                self.assertFalse(self.isolation._adapter_owned(root / "package" / "core.py"))
                self.assertEqual(self.isolation._root_production_files(), [root / "root.py"])
                self.assertEqual(
                    set(self.isolation._package_production_files()),
                    {
                        adapter_package / "rate.py",
                        adapter_package / "read.py",
                        adapter_package / "__pycache__" / "cached.py",
                        root / "package" / "core.py",
                        root / "package" / "__pycache__" / "cached.py",
                        root / "scripts" / "tool.py",
                    },
                )
                self.assertEqual(
                    self.isolation._production_files(),
                    [
                        root / "package" / "core.py",
                        root / "root.py",
                        root / "scripts" / "tool.py",
                    ],
                )
                self.assertEqual(
                    self.isolation._gateway_files(),
                    [adapter, adapter_package / "rate.py", adapter_package / "read.py"],
                )
                self.assertEqual(
                    set(self.isolation._scanned_text_files()),
                    {
                        root / "README.md",
                        root / "root.sh",
                        root / "scripts" / "nested" / "tool.sh",
                        root / "scripts" / "notes.md",
                        root / "deploy" / "service" / "run",
                        root / "deploy" / "notes.md",
                        root / "docs" / "design.md",
                        root / "examples" / "example.md",
                    },
                )
                self.assertEqual(
                    set(self.isolation._matching_roots(("scripts",), ("*.sh", "*.md"))),
                    {
                        root / "scripts" / "nested" / "tool.sh",
                        root / "scripts" / "notes.md",
                        root / "scripts" / "__pycache__" / "cached.md",
                        root / "scripts" / "directory.md",
                    },
                )
                self.assertEqual(
                    set(self.isolation._recursive_matching_files(root / "scripts", ("*.sh", "*.md"))),
                    {
                        root / "scripts" / "nested" / "tool.sh",
                        root / "scripts" / "notes.md",
                        root / "scripts" / "__pycache__" / "cached.md",
                        root / "scripts" / "directory.md",
                    },
                )
                self.assertEqual(
                    self.isolation._direct_matching_files(root, ("*.sh",)),
                    [root / "root.sh"],
                )

    def test_isolation_ast_and_cli_helpers_have_exact_boundaries(self) -> None:
        def list_items(source: str) -> list[ast.expr]:
            expression = ast.parse(source, mode="eval")
            self.assertIsInstance(expression.body, ast.List)
            assert isinstance(expression.body, ast.List)
            return expression.body.elts

        self.assertTrue(self.isolation._absolute_import_forbidden("dbus.mainloop.glib"))
        self.assertTrue(self.isolation._absolute_import_forbidden("dbus.one.two"))
        self.assertTrue(self.isolation._absolute_import_forbidden("vedbus"))
        self.assertFalse(self.isolation._absolute_import_forbidden("venus_evcharger.dbus_gateway"))
        self.assertEqual(self.isolation._call_name(ast.parse("call", mode="eval").body), "call")
        self.assertEqual(self.isolation._call_name(ast.parse("proxy.call", mode="eval").body), "call")
        self.assertEqual(self.isolation._call_name(ast.parse("1", mode="eval").body), "")

        literal = ast.Constant("/usr/bin/dbus-send")
        self.assertTrue(self.isolation._forbidden_cli_literal(literal))
        self.assertEqual(self.isolation._cli_literal_name(literal), "dbus-send")
        self.assertEqual(self.isolation._cli_literal_name(ast.Constant(3)), "")
        self.assertEqual(self.isolation._cli_literal_name(ast.Name(id="tool")), "")
        self.assertEqual(self.isolation._literal_text(ast.Constant("call")), "call")
        self.assertEqual(self.isolation._literal_text(ast.Constant(3)), "")

        self.assertFalse(self.isolation._forbidden_cli_sequence([]))
        self.assertTrue(self.isolation._forbidden_cli_sequence(list_items('["dbus", "-y"]')))
        self.assertFalse(self.isolation._forbidden_cli_sequence(list_items('["dbus"]')))
        self.assertEqual(self.isolation._command_argument(list_items('["dbus"]')), "")
        self.assertEqual(self.isolation._command_argument(list_items('["dbus", "-s"]')), "-s")
        cases = {
            ("dbus", "-y"): True,
            ("dbus", "--unknown"): False,
            ("dbus-send", "--system"): True,
            ("dbus-send", "--custom"): True,
            ("dbus-send", "destination"): False,
            ("dbus-monitor", ""): True,
            ("gdbus", "call"): True,
            ("busctl", "get-property"): True,
            ("python3", "dbus"): False,
        }
        for arguments, expected in cases.items():
            with self.subTest(arguments=arguments):
                self.assertIs(self.isolation._forbidden_cli_command(*arguments), expected)

    def test_isolation_visitors_report_exact_import_call_symbol_and_command_kinds(self) -> None:
        source = (
            "import dbus, os\n"
            "from vedbus import VeDbusService as Service\n"
            "from .dbus import local\n"
            "from . import sibling\n"
            "proxy.GetValue()\n"
            "shutil.which('gdbus')\n"
            "distutils.spawn.find_executable('dbus-send')\n"
            "subprocess.run('gdbus')\n"
            "['dbus', '-y']\n"
            "('gdbus', 'call')\n"
            "VeDbusService\n"
        )
        path = self.isolation.REPO / "venus_evcharger" / "outside.py"
        visitor = self.isolation.DbusIsolationVisitor(path)
        visitor.visit(ast.parse(source))

        self.assertEqual(
            visitor.violations,
            [
                "venus_evcharger/outside.py:1: forbidden import dbus",
                "venus_evcharger/outside.py:2: forbidden import from vedbus",
                "venus_evcharger/outside.py:5: forbidden DBus call GetValue",
                "venus_evcharger/outside.py:6: forbidden DBus CLI lookup",
                "venus_evcharger/outside.py:7: forbidden DBus CLI lookup",
                "venus_evcharger/outside.py:9: forbidden DBus CLI command",
                "venus_evcharger/outside.py:10: forbidden DBus CLI command",
                "venus_evcharger/outside.py:11: forbidden DBus symbol VeDbusService",
            ],
        )
        no_line = self.isolation.DbusIsolationVisitor(path)
        no_line._add(ast.Load(), "synthetic")
        self.assertEqual(no_line.violations, ["venus_evcharger/outside.py:0: synthetic"])

    def test_isolation_file_parsers_cover_valid_syntax_errors_cli_lines_and_self_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "valid.py"
            invalid = root / "invalid.py"
            text_file = root / "commands.md"
            valid.write_text("# umlaut: ü\nimport dbus\n", encoding="utf-8")
            invalid.write_text("if:\n", encoding="utf-8")
            text_file.write_text("safe\ndbus -y service /Path GetValue\n", encoding="utf-8")

            with patch.object(self.isolation, "REPO", root):
                self.assertEqual(
                    self.isolation._violations_for(valid),
                    ["valid.py:2: forbidden import dbus"],
                )
                syntax = self.isolation._violations_for(invalid)
                self.assertEqual(len(syntax), 1)
                self.assertTrue(syntax[0].startswith("invalid.py:1: unable to parse:"))
                ownership_syntax = self.isolation._gateway_ownership_violations(invalid)
                self.assertEqual(len(ownership_syntax), 1)
                self.assertTrue(ownership_syntax[0].startswith("invalid.py:1: unable to parse:"))
                self.assertEqual(
                    self.isolation._gateway_ownership_violations(valid),
                    ["valid.py:2: gateway DBus import dbus belongs to another module"],
                )
                self.assertEqual(
                    self.isolation._cli_violations(text_file),
                    ["commands.md:2: forbidden DBus CLI dbus -y"],
                )
                self.assertEqual(self.isolation._read_text(valid), "# umlaut: ü\nimport dbus\n")
                self.assertIsInstance(self.isolation._parse_python(valid), ast.Module)
                self.assertEqual(
                    self.isolation._syntax_violation(invalid, SyntaxError("synthetic")),
                    "invalid.py:0: unable to parse: synthetic",
                )
            self.assertEqual(self.isolation._cli_violations(Path(self.isolation.__file__)), [])

    def test_isolation_aggregation_reporting_and_main_preserve_all_findings(self) -> None:
        first = Path("first.py")
        second = Path("second.py")
        with (
            patch.object(self.isolation, "_production_files", return_value=[first, second]),
            patch.object(
                self.isolation,
                "_violations_for",
                side_effect=lambda path: {first: ["p1"], second: ["p2", "p3"]}[path],
            ) as inspect_production,
        ):
            self.assertEqual(self.isolation._production_violations(), ["p1", "p2", "p3"])
        self.assertEqual(inspect_production.call_args_list, [call(first), call(second)])
        with (
            patch.object(self.isolation, "_gateway_files", return_value=[first]),
            patch.object(
                self.isolation,
                "_gateway_ownership_violations",
                side_effect=lambda path: ["g1"] if path == first else [],
            ) as inspect_gateway,
        ):
            self.assertEqual(self.isolation._gateway_violations(), ["g1"])
        inspect_gateway.assert_called_once_with(first)
        with (
            patch.object(self.isolation, "_scanned_text_files", return_value=[second]),
            patch.object(
                self.isolation,
                "_cli_violations",
                side_effect=lambda path: ["t1", "t2"] if path == second else [],
            ) as inspect_text,
        ):
            self.assertEqual(self.isolation._text_violations(), ["t1", "t2"])
        inspect_text.assert_called_once_with(second)
        self.assertEqual(
            self.isolation._collect_violations(
                [first, second],
                lambda path: [path.name, path.suffix],
            ),
            ["first.py", ".py", "second.py", ".py"],
        )
        with (
            patch.object(self.isolation, "_production_violations", return_value=["p"]),
            patch.object(self.isolation, "_gateway_violations", return_value=["g"]),
            patch.object(self.isolation, "_text_violations", return_value=["t"]),
            patch.object(self.isolation, "_rust_observer_violations", return_value=["r"]),
        ):
            self.assertEqual(self.isolation._all_violations(), ["p", "g", "t", "r"])

        error_output = StringIO()
        with redirect_stderr(error_output):
            self.assertEqual(self.isolation._report_violations(["first", "second"]), 1)
        self.assertEqual(
            error_output.getvalue().splitlines(),
            [
                "Direct DBus access is only allowed in dedicated DBus gateway adapter modules.",
                "first",
                "second",
            ],
        )
        self.assertEqual(self.isolation._report_violations([]), 0)
        with (
            patch.object(self.isolation, "_all_violations", return_value=["violation"]) as collect,
            patch.object(self.isolation, "_report_violations", return_value=7) as report,
        ):
            self.assertEqual(self.isolation.main(), 7)
        collect.assert_called_once_with()
        report.assert_called_once_with(["violation"])

    def test_gateway_transport_primitives_have_single_module_owners(self) -> None:
        misplaced_path = self.isolation.REPO / "venus_evcharger" / "dbus_adapter" / "read" / "executor.py"
        visitor = self.isolation.DbusGatewayOwnershipVisitor(misplaced_path)
        visitor.visit(
            ast.parse(
                "import dbus\n"
                "from dbus.mainloop.glib import DBusGMainLoop\n"
                "from vedbus import VeDbusService\n"
                "dbus.Interface(obj, interface)\n"
                "connection.get_object(service, path)\n"
                "connection.call_async(service, path)\n"
                "proxy.GetValue()\n"
            )
        )

        self.assertEqual(
            visitor.violations,
            [
                "venus_evcharger/dbus_adapter/read/executor.py:1: "
                "gateway DBus import dbus belongs to another module",
                "venus_evcharger/dbus_adapter/read/executor.py:2: "
                "gateway DBus import dbus.mainloop.glib belongs to another module",
                "venus_evcharger/dbus_adapter/read/executor.py:3: "
                "gateway DBus import vedbus belongs to another module",
                "venus_evcharger/dbus_adapter/read/executor.py:4: "
                "gateway DBus call Interface belongs to another module",
                "venus_evcharger/dbus_adapter/read/executor.py:5: "
                "gateway DBus call get_object belongs to another module",
                "venus_evcharger/dbus_adapter/read/executor.py:6: "
                "gateway DBus call call_async belongs to another module",
                "venus_evcharger/dbus_adapter/read/executor.py:7: "
                "gateway DBus call GetValue belongs to another module",
            ],
        )

        relative_import = self.isolation.DbusGatewayOwnershipVisitor(misplaced_path)
        relative_import.visit(ast.parse("from . import dbus\nfrom .dbus import local\n"))
        self.assertEqual(relative_import.violations, [])

        no_line = self.isolation.DbusGatewayOwnershipVisitor(misplaced_path)
        no_line._add(ast.Load(), "synthetic")
        self.assertEqual(
            no_line.violations,
            ["venus_evcharger/dbus_adapter/read/executor.py:0: synthetic"],
        )

        allowed_sources = {
            self.isolation.CONNECTION_MANAGER: (
                "import dbus\ndbus.SystemBus(private=True)\ncall_async(service, path)\n"
            ),
            self.isolation.PUBLICATION_REGISTRY: ("from vedbus import VeDbusService\nVeDbusService(name)\n"),
            self.isolation.PROCESS_LOOP: (
                "from dbus.mainloop.glib import DBusGMainLoop\nDBusGMainLoop(set_as_default=True)\n"
            ),
            self.isolation.DBUS_ERROR_CLASSIFIER: "import dbus\n",
        }
        for path, source in allowed_sources.items():
            with self.subTest(path=path.name):
                allowed = self.isolation.DbusGatewayOwnershipVisitor(path)
                allowed.visit(ast.parse(source))
                self.assertEqual(allowed.violations, [])

        direct_method = self.isolation.DbusGatewayOwnershipVisitor(misplaced_path)
        direct_method.visit(ast.parse("proxy.SetValue(value)\n"))
        self.assertEqual(len(direct_method.violations), 1)


if __name__ == "__main__":
    unittest.main()
