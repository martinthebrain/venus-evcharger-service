# SPDX-License-Identifier: GPL-3.0-or-later
"""Remote Raspberry-Pi deployment and service helpers for the release gate."""

from __future__ import annotations

import argparse

from pi_gateway_release_gate_common import PiSession


def remote_compile(pi: PiSession, remote_dir: str) -> None:
    modules = " ".join(
        [
            "venus_evcharger_dbus_adapter.py",
            "venus_evcharger_service.py",
            "venus_evcharger_auto_input_helper.py",
            "venus_evcharger/dbus_gateway.py",
            "venus_evcharger/dbus_adapter/read/executor.py",
            "venus_evcharger/dbus_adapter/write/scheduler.py",
            "scripts/dev/dbus_gateway_chaos.py",
        ]
    )
    pi.ssh(
        f"cd {remote_dir!r} && python3 scripts/dev/check_python_syntax_venus.py "
        f"venus_evcharger/dbus_adapter venus_evcharger/ipc {modules}",
        timeout=60.0,
    )


def remote_isolation(pi: PiSession, remote_dir: str) -> None:
    pi.ssh(f"cd {remote_dir!r} && python3 scripts/dev/check_dbus_isolation.py", timeout=60.0)


def remote_gateway_chaos(pi: PiSession, remote_dir: str) -> None:
    pi.ssh(f"cd {remote_dir!r} && python3 scripts/dev/dbus_gateway_chaos.py", timeout=120.0)


def configure_remote(pi: PiSession, args: argparse.Namespace, host_value: str) -> None:
    if not host_value:
        return
    script = f"""
cd {args.remote_dir!r}
python3 - <<'PY'
from pathlib import Path
path = Path("deploy/venus/config.venus_evcharger.ini")
text = path.read_text()
updates = {{
    "Host": {host_value!r},
    "DeviceInstance": {str(args.device_instance)!r},
    "DbusGatewayRunDir": {args.gateway_run_dir!r},
}}
lines = []
seen = set()
for line in text.splitlines():
    key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else ""
    if key in updates:
        lines.append(f"{{key}}={{updates[key]}}")
        seen.add(key)
    else:
        lines.append(line)
for key, value in updates.items():
    if key not in seen:
        lines.append(f"{{key}}={{value}}")
path.write_text("\\n".join(lines) + "\\n")
PY
"""
    pi.ssh(script, timeout=20.0)


def restart_remote_services(pi: PiSession) -> None:
    pi.ssh(
        "svc -d /service/dbus-venus-evcharger /service/dbus-venus-evcharger-dbus-adapter "
        "/service/dbus-venus-evcharger-observer 2>/dev/null || true; "
        "sleep 2; "
        "python3 - <<'PY'\n"
        "import os, signal, subprocess\n"
        "markers = (\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_service.py',\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_dbus_adapter.py',\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_observer.py',\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_auto_input_helper.py',\n"
        ")\n"
        "protected = {os.getpid(), os.getppid()}\n"
        "out = subprocess.check_output(['ps', 'w'], text=True)\n"
        "for line in out.splitlines():\n"
        "    if not any(marker in line for marker in markers):\n"
        "        continue\n"
        "    fields = line.split(None, 1)\n"
        "    if not fields:\n"
        "        continue\n"
        "    try:\n"
        "        pid = int(fields[0])\n"
        "    except ValueError:\n"
        "        continue\n"
        "    if pid not in protected:\n"
        "        os.kill(pid, signal.SIGTERM)\n"
        "PY\n"
        "sleep 1; "
        "rm -rf /run/venus-evcharger/dbus-commands /run/venus-evcharger/core-commands; "
        "mkdir -p /run/venus-evcharger/dbus-commands /run/venus-evcharger/core-commands; "
        "svc -u /service/dbus-venus-evcharger-dbus-adapter /service/dbus-venus-evcharger "
        "/service/dbus-venus-evcharger-observer 2>/dev/null || true",
        timeout=30.0,
    )


def assert_single_remote_instance(pi: PiSession) -> None:
    script = r"""
python3 - <<'PY'
import subprocess
patterns = {
    "service": "python3 /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_service.py",
    "adapter": "python3 /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_dbus_adapter.py",
    "observer": "python3 /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_observer.py",
}
out = subprocess.check_output(["ps", "w"], text=True)
for label, marker in patterns.items():
    count = sum(1 for line in out.splitlines() if marker in line and "grep" not in line)
    print(f"{label}={count}")
    if count != 1:
        raise SystemExit(10)
helper_marker = "python3 -u /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_auto_input_helper.py"
helpers = sum(1 for line in out.splitlines() if helper_marker in line and "grep" not in line)
print(f"helper={helpers}")
if helpers > 1:
    raise SystemExit(11)
PY
"""
    pi.ssh(script, timeout=15.0)
