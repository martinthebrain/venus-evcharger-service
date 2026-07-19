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
    def test_reconciles_links_and_only_kills_deleted_project_processes(self) -> None:
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
                (script_dir / service_dir).mkdir()

            stale_process = proc_root / "123"
            unrelated_process = proc_root / "456"
            stale_process.mkdir(parents=True)
            unrelated_process.mkdir(parents=True)
            (stale_process / "cwd").symlink_to(f"{script_dir}/service_venus_evcharger (deleted)")
            (unrelated_process / "cwd").symlink_to("/tmp/unrelated (deleted)")
            service_log = root / "service.log"
            kill_log = root / "kill.log"

            command = f"""
SCRIPT_DIR={script_dir!s}
SERVICE_ROOT={service_root!s}
PROC_ROOT={proc_root!s}
SERVICE_NAME=dbus-venus-evcharger
DBUS_ADAPTER_SERVICE_NAME=dbus-venus-evcharger-dbus-adapter
OBSERVER_SERVICE_NAME=dbus-venus-evcharger-observer
SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger"
DBUS_ADAPTER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_dbus_adapter"
OBSERVER_SERVICE_DIR="$SCRIPT_DIR/service_venus_evcharger_observer"
VENUS_EVCHARGER_SERVICE_SETTLE_SECONDS=0
VENUS_EVCHARGER_ADAPTER_START_SECONDS=0
svc() {{ printf '%s\n' "$*" >> {service_log!s}; }}
kill() {{ printf '%s\n' "$*" >> {kill_log!s}; }}
. "$SCRIPT_DIR/service_lifecycle.sh"
venus_cleanup_deleted_service_processes
venus_register_service_links
venus_start_services
venus_stop_and_deregister_services
"""
            subprocess.run(["sh", "-c", command], check=True, env=os.environ.copy())

            self.assertEqual(kill_log.read_text(encoding="utf-8").splitlines(), ["123", "-KILL 123"])
            self.assertEqual(
                service_log.read_text(encoding="utf-8").splitlines(),
                [
                    f"-u {service_root}/dbus-venus-evcharger-dbus-adapter",
                    f"-u {service_root}/dbus-venus-evcharger",
                    f"-u {service_root}/dbus-venus-evcharger-observer",
                    f"-d {service_root}/dbus-venus-evcharger-observer",
                    f"-d {service_root}/dbus-venus-evcharger",
                    f"-d {service_root}/dbus-venus-evcharger-dbus-adapter",
                ],
            )
            self.assertEqual(list(service_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
