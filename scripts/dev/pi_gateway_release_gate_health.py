# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway health assertions for the Raspberry-Pi release gate."""

from __future__ import annotations

import json
import time
from typing import Any

from pi_gateway_release_gate_common import GateFailure, PiSession

MAX_OLD_COMMAND_AGE_SECONDS = 30.0
MAX_PENDING_COMMANDS = 80
MAX_TICK_GAP_MS = 1500.0
MAX_FRESH_CORE_VALUE_AGE_SECONDS = 10.0
MAX_HEALTH_HISTORY_AGE_SECONDS = 90.0
MAX_COMMAND_LIFECYCLE_AGE_SECONDS = 300.0
LIFECYCLE_STATES = {"queued", "coalesced", "applied", "dropped", "deferred", "expired"}


def wait_for_healthy_gateway(pi: PiSession, run_dir: str, *, timeout: float, poll_seconds: float) -> dict[str, Any]:
    deadline = time.time() + max(0.1, float(timeout))
    last_health: dict[str, Any] = {}
    last_failures: list[str] = ["health was not checked"]
    while time.time() < deadline:
        last_health = _health(pi, run_dir)
        last_failures = _health_failures(last_health)
        if not last_failures:
            return last_health
        time.sleep(max(0.1, min(float(poll_seconds), deadline - time.time())))
    raise GateFailure("; ".join(last_failures) + f"\nlast_health={json.dumps(last_health, sort_keys=True)}")


def _health(pi: PiSession, run_dir: str) -> dict[str, Any]:
    raw = pi.ssh(f"cat {run_dir.rstrip('/') + '/dbus-health.json'!r}", timeout=8.0)
    payload = json.loads(raw)
    health = payload.get("dbus_health")
    if not isinstance(health, dict):
        raise GateFailure("health.json has no dbus_health object")
    return health


def assert_recent_health_history(pi: PiSession, run_dir: str) -> None:
    payload = _latest_jsonl_payload(pi, _run_path(run_dir, "dbus-health-history.jsonl"))
    _raise_payload_failures(_health_history_failures(payload), payload)


def assert_recent_command_lifecycle(pi: PiSession, run_dir: str) -> None:
    payload = _latest_jsonl_payload(pi, _run_path(run_dir, "dbus-command-lifecycle.jsonl"))
    _raise_payload_failures(_command_lifecycle_failures(payload), payload)


def _health_history_failures(payload: dict[str, Any]) -> list[str]:
    age = _payload_age(payload, label="health history")
    failures: list[str] = []
    if age > MAX_HEALTH_HISTORY_AGE_SECONDS:
        failures.append(f"health history is stale: age={age:.1f}s")
    if _payload_text(payload, "state") not in {"ok", "degraded", "protective"}:
        failures.append(f"health history has unexpected state {payload.get('state')!r}")
    if _payload_text(payload, "backpressure") not in {"ok", "congested", "slow", "protective"}:
        failures.append(f"health history has unexpected backpressure {payload.get('backpressure')!r}")
    if not isinstance(payload.get("cache_freshness"), dict):
        failures.append("health history has no cache_freshness object")
    return failures


def _command_lifecycle_failures(payload: dict[str, Any]) -> list[str]:
    age = _payload_age(payload, label="command lifecycle")
    failures: list[str] = []
    if age > MAX_COMMAND_LIFECYCLE_AGE_SECONDS:
        failures.append(f"command lifecycle is stale: age={age:.1f}s")
    if _payload_text(payload, "state") not in LIFECYCLE_STATES:
        failures.append(f"command lifecycle has unexpected state {payload.get('state')!r}")
    if not _payload_text(payload, "queue_class"):
        failures.append("command lifecycle has no queue_class")
    if not _payload_text(payload, "kind"):
        failures.append("command lifecycle has no kind")
    return failures


def _raise_payload_failures(failures: list[str], payload: dict[str, Any]) -> None:
    if failures:
        raise GateFailure("; ".join(failures) + f"\npayload={json.dumps(payload, sort_keys=True)}")


def _payload_text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _health_failures(health: dict[str, Any]) -> list[str]:
    queues = health.get("queues", {}) if isinstance(health.get("queues"), dict) else {}
    eventloop = health.get("eventloop", {}) if isinstance(health.get("eventloop"), dict) else {}
    freshness = health.get("cache_freshness", {}) if isinstance(health.get("cache_freshness"), dict) else {}
    failures: list[str] = []
    failures.extend(_health_state_failures(health))
    failures.extend(_health_queue_failures(queues))
    failures.extend(_health_eventloop_failures(eventloop))
    failures.extend(_health_freshness_failures(freshness))
    return failures


def _run_path(run_dir: str, filename: str) -> str:
    return run_dir.rstrip("/") + "/" + filename


def _latest_jsonl_payload(pi: PiSession, path: str) -> dict[str, Any]:
    raw = pi.ssh(_latest_jsonl_script(path), timeout=8.0)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise GateFailure(f"{path} latest JSONL entry is not an object")
    return payload


def _latest_jsonl_script(path: str) -> str:
    return (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(" + repr(path) + ")\n"
        "last = ''\n"
        "if not path.exists():\n"
        "    raise SystemExit(f'missing {path}')\n"
        "with path.open(encoding='utf-8') as handle:\n"
        "    for line in handle:\n"
        "        if line.strip():\n"
        "            last = line.strip()\n"
        "if not last:\n"
        "    raise SystemExit(f'empty {path}')\n"
        "print(last)\n"
        "PY\n"
    )


def _payload_age(payload: dict[str, Any], *, label: str) -> float:
    try:
        return max(0.0, time.time() - float(payload.get("at")))
    except (TypeError, ValueError) as error:
        raise GateFailure(f"{label} has invalid timestamp: {payload.get('at')!r}") from error


def _health_state_failures(health: dict[str, Any]) -> list[str]:
    if str(health.get("state")) in {"ok", "degraded"}:
        return []
    return [f"unexpected gateway state {health.get('state')!r}"]


def _health_queue_failures(queues: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if float(queues.get("oldest_command_age_s", 0.0) or 0.0) > MAX_OLD_COMMAND_AGE_SECONDS:
        failures.append(f"old command age {queues.get('oldest_command_age_s')}s")
    if int(queues.get("pending_command_count", 0) or 0) > MAX_PENDING_COMMANDS:
        failures.append(f"too many pending commands {queues.get('pending_command_count')}")
    return failures


def _health_eventloop_failures(eventloop: dict[str, Any]) -> list[str]:
    if float(eventloop.get("max_tick_gap_ms_60s", 0.0) or 0.0) <= MAX_TICK_GAP_MS:
        return []
    return [f"event-loop gap {eventloop.get('max_tick_gap_ms_60s')}ms"]


def _health_freshness_failures(freshness: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for key in ("grid_power_w", "pv_power_w"):
        failure = _health_freshness_failure(freshness, key)
        if failure:
            failures.append(failure)
    return failures


def _health_freshness_failure(freshness: dict[str, Any], key: str) -> str:
    status = freshness.get(f"{key}_status")
    age = float(freshness.get(f"{key}_age_s", 0.0) or 0.0)
    return f"{key} age {age}s" if status == "fresh" and age > MAX_FRESH_CORE_VALUE_AGE_SECONDS else ""
