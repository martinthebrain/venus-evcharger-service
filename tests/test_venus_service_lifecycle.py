# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_SCRIPT = REPO_ROOT / "deploy/venus/service_lifecycle.sh"


class TestVenusServiceLifecycle(unittest.TestCase):
    def test_reconciles_links_and_only_kills_project_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script_dir = root / "deploy/venus"
            service_root = root / "service"
            proc_root = root / "proc"
            lifecycle_copy = script_dir / "service_lifecycle.sh"
            lifecycle_copy.parent.mkdir(parents=True)
            lifecycle_copy.write_text(LIFECYCLE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")

            for service_dir in (
                "service_venus_evcharger",
                "service_venus_evcharger_dbus_adapter",
                "service_venus_evcharger_observer",
            ):
                supervisor_dir = script_dir / service_dir / "supervise"
                supervisor_dir.mkdir(parents=True)
                os.mkfifo(supervisor_dir / "ok")

            stale_process = proc_root / "123"
            unrelated_process = proc_root / "456"
            managed_process = proc_root / "789"
            live_supervisor = proc_root / "321"
            stale_process.mkdir(parents=True)
            unrelated_process.mkdir(parents=True)
            managed_process.mkdir(parents=True)
            live_supervisor.mkdir(parents=True)
            (stale_process / "cwd").symlink_to(f"{script_dir}/service_venus_evcharger (deleted)")
            (unrelated_process / "cwd").symlink_to("/tmp/unrelated (deleted)")
            (managed_process / "cwd").symlink_to(script_dir)
            (live_supervisor / "cwd").symlink_to(script_dir / "service_venus_evcharger" / "log")
            (managed_process / "cmdline").write_bytes(
                b"python3\0" + os.fsencode(root / "venus_evcharger_dbus_adapter.py") + b"\0/data/venus-evcharger/\0"
            )
            (unrelated_process / "cmdline").write_bytes(b"python3\0/tmp/unrelated.py\0")
            service_log = root / "service.log"
            kill_log = root / "kill.log"

            command = f"""
SCRIPT_DIR={script_dir!s}
SERVICE_ROOT={service_root!s}
PROC_ROOT={proc_root!s}
REPO_DIR={root!s}
SERVICE_NAME=dbus-venus-evcharger
DBUS_ADAPTER_SERVICE_NAME=dbus-venus-evcharger-dbus-adapter
OBSERVER_SERVICE_NAME=dbus-venus-evcharger-observer
SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger"
DBUS_ADAPTER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_dbus_adapter"
OBSERVER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_observer"
VENUS_EVCHARGER_SERVICE_SETTLE_SECONDS=0
VENUS_EVCHARGER_ADAPTER_START_SECONDS=0
VENUS_EVCHARGER_SUPERVISOR_WAIT_SECONDS=0
svc() {{ printf '%s\n' "$*" >> {service_log!s}; }}
svstat() {{ return 0; }}
kill() {{ printf '%s\n' "$*" >> {kill_log!s}; }}
. "$SCRIPT_DIR/service_lifecycle.sh"
venus_reconcile_services
"""
            subprocess.run(["sh", "-c", command], check=True, env=os.environ.copy())

            self.assertEqual(
                kill_log.read_text(encoding="utf-8").splitlines(),
                ["123", "321", "789", "-KILL 123", "-KILL 321", "-KILL 789"],
            )
            self.assertEqual(
                service_log.read_text(encoding="utf-8").splitlines(),
                [
                    f"-d {service_root}/dbus-venus-evcharger-observer",
                    f"-d {service_root}/dbus-venus-evcharger",
                    f"-d {service_root}/dbus-venus-evcharger-dbus-adapter",
                    f"-u {service_root}/dbus-venus-evcharger-dbus-adapter",
                    f"-u {service_root}/dbus-venus-evcharger",
                    f"-u {service_root}/dbus-venus-evcharger-observer",
                ],
            )
            self.assertEqual(
                sorted(path.name for path in service_root.iterdir()),
                [
                    "dbus-venus-evcharger",
                    "dbus-venus-evcharger-dbus-adapter",
                    "dbus-venus-evcharger-observer",
                ],
            )

    def test_waits_for_responsive_supervisor_before_start_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service_root = root / "service"
            service_path = service_root / "adapter"
            supervisor_dir = service_path / "supervise"
            supervisor_dir.mkdir(parents=True)
            os.mkfifo(supervisor_dir / "ok")
            readiness_log = root / "readiness.log"
            service_log = root / "service.log"
            command = f"""
SERVICE_ROOT={service_root!s}
VENUS_EVCHARGER_SUPERVISOR_WAIT_SECONDS=3
sleep() {{ :; }}
svstat() {{
    printf 'check\n' >> {readiness_log!s}
    [ "$(wc -l < {readiness_log!s})" -ge 3 ]
}}
svc() {{ printf '%s\n' "$*" >> {service_log!s}; }}
. {LIFECYCLE_SCRIPT!s}
venus_service_up adapter
"""

            result = subprocess.run(
                ["sh", "-c", command],
                check=False,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            self.assertEqual(readiness_log.read_text(encoding="utf-8").splitlines(), ["check", "check", "check"])
            self.assertEqual(service_log.read_text(encoding="utf-8").splitlines(), [f"-u {service_path}"])


if __name__ == "__main__":
    unittest.main()
