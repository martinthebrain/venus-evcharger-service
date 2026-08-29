#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run focused process-level Python/Rust auto-input parity scenarios."""

from __future__ import annotations

import argparse
import copy
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

repository_root = Path(__file__).resolve().parents[2]
if str(repository_root) not in sys.path:
    sys.path.insert(0, str(repository_root))

from scripts.dev.auto_input_helper_differential_harness import (
    PROCESS_TIMEOUT_SECONDS,
    REPO_ROOT,
    base_config,
    compare_scenario,
    external_config,
    write_gateway_snapshot,
)
from scripts.dev.auto_input_helper_differential_scenarios import (
    gateway_scenarios,
    run_connector_scenarios,
)
from scripts.dev.auto_input_helper_differential_servers import (
    QuietJsonHandler,
    controlled_json_server,
)
from scripts.dev.compare_auto_input_helper_snapshots import SnapshotParityError, compare_snapshots, load_snapshot


def _external_source(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    sources = payload.get("battery_sources")
    if not isinstance(sources, list):
        return None
    for value in cast(list[object], sources):
        if not isinstance(value, dict):
            continue
        source = cast(dict[str, object], value)
        if source.get("source_id") == "external":
            return source
    return None


def _wait_snapshot(
    path: Path,
    predicate: Callable[[Mapping[str, object]], bool],
    deadline: float,
) -> dict[str, object]:
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            last_payload = load_snapshot(path)
        except SnapshotParityError:
            time.sleep(0.05)
            continue
        if predicate(last_payload):
            return last_payload
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for scenario snapshot {path}: {last_payload!r}")


def _start_helper(
    executable: Sequence[str],
    config_path: Path,
    snapshot_path: Path,
    runtime_id: str,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            *executable,
            str(config_path),
            str(snapshot_path),
            str(os.getpid()),
            "7",
            runtime_id,
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop_helper(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3.0)


def _recovery_source_succeeded(payload: Mapping[str, object]) -> bool:
    return (_external_source(payload) or {}).get("poll_status") == "success"


def _recovery_source_uses_last_good(payload: Mapping[str, object]) -> bool:
    source = _external_source(payload) or {}
    failures = source.get("consecutive_failures")
    return isinstance(failures, int) and failures >= 1 and source.get("contributing") is True


def _recovery_source_restored(payload: Mapping[str, object]) -> bool:
    source = _external_source(payload) or {}
    return source.get("poll_status") == "success" and source.get("consecutive_failures") == 0


def _prepare_recovery_run(execution_root: Path, base_url: str) -> tuple[Path, Path]:
    gateway_dir = execution_root / "gateway"
    execution_root.mkdir(parents=True)
    write_gateway_snapshot(
        gateway_dir,
        {"grid": 0.0, "pv": 100.0, "soc": 60.0, "battery_power": 0.0},
        age_seconds=0.0,
        observed_epoch=None,
    )
    connector = execution_root / "source.ini"
    connector.write_text(
        f"""[Adapter]
BaseUrl={base_url}
RequestTimeoutSeconds=0.1
[EnergyRequest]
Url=/energy
[EnergyResponse]
SocPath=data.soc
UsableCapacityWhPath=data.capacity_wh
PvInputPowerPath=data.pv_w
OnlinePath=data.online
""",
        encoding="utf-8",
    )
    config_path = execution_root / "config.ini"
    config_path.write_text(
        base_config(gateway_dir) + external_config("template-http-hybrid", connector),
        encoding="utf-8",
    )
    return config_path, execution_root / "snapshot.json"


def _capture_recovery_states(
    executable: Sequence[str],
    runtime_name: str,
    config_path: Path,
    snapshot_path: Path,
    handler: type[QuietJsonHandler],
) -> tuple[dict[str, object], dict[str, object]]:
    process = _start_helper(executable, config_path, snapshot_path, runtime_name)
    try:
        deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
        _wait_snapshot(snapshot_path, _recovery_source_succeeded, deadline)
        handler.delay_seconds = 0.5
        last_good = _wait_snapshot(snapshot_path, _recovery_source_uses_last_good, deadline)
        handler.delay_seconds = 0.0
        recovered = _wait_snapshot(snapshot_path, _recovery_source_restored, deadline)
        return copy.deepcopy(last_good), copy.deepcopy(recovered)
    finally:
        _stop_helper(process)


def _run_timeout_last_good_recovery_once(
    executable: Sequence[str],
    runtime_name: str,
    root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    execution_root = root / runtime_name
    payload = {
        "data": {
            "soc": 70.0,
            "capacity_wh": 8000.0,
            "pv_w": 2100.0,
            "online": True,
        }
    }
    with controlled_json_server(payload) as (base_url, handler):
        config_path, snapshot_path = _prepare_recovery_run(execution_root, base_url)
        return _capture_recovery_states(
            executable,
            runtime_name,
            config_path,
            snapshot_path,
            handler,
        )


def _normalize_named_error(key: str, value: object) -> object:
    if key == "last_error" and isinstance(value, str) and value:
        return "<error>"
    return _normalize_error_details(value)


def _normalize_error_details(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        return {key: _normalize_named_error(key, item) for key, item in mapping.items()}
    if isinstance(value, list):
        return [_normalize_error_details(item) for item in cast(list[object], value)]
    return value


def _assert_recovery_parity(
    label: str,
    python_payload: Mapping[str, object],
    rust_payload: Mapping[str, object],
) -> None:
    left = _normalize_error_details(python_payload)
    right = _normalize_error_details(rust_payload)
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise RuntimeError(f"scenario {label} did not produce object snapshots")
    result = compare_snapshots(
        cast(dict[str, object], left),
        cast(dict[str, object], right),
    )
    if not result.equal:
        details = "\n".join(f"  - {item}" for item in result.differences)
        raise RuntimeError(f"scenario {label} differs:\n{details}")


def _run_timeout_last_good_recovery(rust_binary: Path, root: Path) -> None:
    python_states = _run_timeout_last_good_recovery_once(
        (sys.executable, str(REPO_ROOT / "venus_evcharger_auto_input_helper.py")),
        "python-timeout-recovery",
        root,
    )
    rust_states = _run_timeout_last_good_recovery_once(
        (str(rust_binary),),
        "rust-timeout-recovery",
        root,
    )
    _assert_recovery_parity("last-good-after-timeout", python_states[0], rust_states[0])
    _assert_recovery_parity("recovered", python_states[1], rust_states[1])


def run(rust_binary: Path) -> None:
    if not rust_binary.is_file() or not os.access(rust_binary, os.X_OK):
        raise RuntimeError(f"Rust helper binary is not executable: {rust_binary}")
    with tempfile.TemporaryDirectory(prefix="auto-input-differential-") as temp_dir:
        root = Path(temp_dir)
        for scenario in gateway_scenarios():
            compare_scenario(scenario, rust_binary, root)
        run_connector_scenarios(rust_binary, root)
        _run_timeout_last_good_recovery(rust_binary, root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-binary", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(args.rust_binary.resolve())
    except (OSError, RuntimeError, SnapshotParityError, subprocess.TimeoutExpired) as error:
        print(f"Auto-input Python/Rust differential failed: {error}", file=sys.stderr)
        return 1
    print("Auto-input Python/Rust differential scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
