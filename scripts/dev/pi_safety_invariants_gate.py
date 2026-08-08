#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run safety-oriented invariant, fuzz, and scenario checks on a Raspberry Pi.

The gate deploys the current workspace into an isolated remote directory and
does not restart or modify the EV charger services. It exercises deterministic
invariant tests, randomized stress tests, and offline DBus-gateway chaos
scenarios on the Pi hardware so CPU, filesystem, Python version, and SSH
transport differences are part of the check.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pi_gateway_release_gate_common import GateFailure, PiSession
from pi_gateway_release_gate_deploy import deploy_repo


@dataclass(frozen=True)
class SafetyStep:
    name: str
    command: str
    timeout_seconds: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run EV charger safety invariant checks on a Raspberry Pi.")
    parser.add_argument("--pi", required=True)
    parser.add_argument("--ssh-config", default="/dev/null")
    parser.add_argument(
        "--remote-dir",
        # This is an isolated throwaway directory on the dedicated test Pi.
        default="/tmp/venus-evcharger-safety-gate",  # nosec B108
    )
    parser.add_argument("--skip-deploy", action="store_true", help="Reuse the existing --remote-dir contents.")
    parser.add_argument("--stress-iters", type=int, default=800)
    parser.add_argument("--stress-threads", type=int, default=4)
    return parser


def safety_steps(args: argparse.Namespace) -> list[SafetyStep]:
    stress_env = (
        f"SHELLY_STRESS_ITERS={max(1, int(args.stress_iters))} "
        f"SHELLY_STRESS_THREADS={max(1, int(args.stress_threads))}"
    )
    return [
        SafetyStep(
            "syntax",
            "python3 scripts/dev/check_python_syntax_venus.py "
            "scripts/dev/dbus_gateway_chaos.py "
            "tests/test_venus_evcharger_invariants.py "
            "tests/test_venus_evcharger_stress.py",
            60.0,
        ),
        SafetyStep(
            "test-import-scope",
            "touch tests/__init__.py",
            10.0,
        ),
        SafetyStep(
            "policy-invariants",
            "PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_venus_evcharger_invariants.py'",
            120.0,
        ),
        SafetyStep(
            "stress-fuzz",
            f"PYTHONPATH=. {stress_env} python3 -m unittest discover -s tests -p 'test_venus_evcharger_stress.py'",
            240.0,
        ),
        SafetyStep(
            "gateway-chaos-scenarios",
            "python3 scripts/dev/dbus_gateway_chaos.py",
            120.0,
        ),
    ]


def run_remote_step(pi: PiSession, remote_dir: str, step: SafetyStep) -> dict[str, Any]:
    started = time.monotonic()
    output = pi.ssh(f"cd {remote_dir!r} && {step.command}", timeout=step.timeout_seconds)
    return {
        "name": step.name,
        "ok": True,
        "duration_s": round(time.monotonic() - started, 3),
        "summary": output.splitlines()[-1:] or [""],
    }


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    pi = PiSession(str(args.pi), ssh_config=str(args.ssh_config))
    remote_dir = str(args.remote_dir)
    if not bool(args.skip_deploy):
        deploy_repo(pi, remote_dir)
    results = [run_remote_step(pi, remote_dir, step) for step in safety_steps(args)]
    return {
        "ok": True,
        "pi": str(args.pi),
        "remote_dir": remote_dir,
        "steps": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = run_gate(args)
    except GateFailure as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
