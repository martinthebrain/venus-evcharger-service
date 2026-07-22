# SPDX-License-Identifier: GPL-3.0-or-later
"""Small local CLI for the Control and State API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from venus_evcharger.control.client import ControlApiClientResponse, LocalControlApiClient
from venus_evcharger.control.cli_parser import build_parser

EXIT_OK = 0
EXIT_REQUEST_FAILED = 1
EXIT_USAGE = 2


def _parse_cli_value(raw_value: str) -> Any:
    normalized = raw_value.strip()
    lowered = normalized.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return raw_value


def _normalized_command_name(raw_name: str) -> str:
    return raw_name.strip().replace("-", "_")


def _client(namespace: argparse.Namespace) -> LocalControlApiClient:
    return _client_for_token(namespace, namespace.token)


def _client_for_token(namespace: argparse.Namespace, token: str) -> LocalControlApiClient:
    return LocalControlApiClient(
        base_url=namespace.url,
        unix_socket_path=namespace.unix_socket,
        bearer_token=token,
        timeout=namespace.timeout,
    )


def _write_json(value: Any, *, compact: bool) -> None:
    if not isinstance(compact, bool):
        raise TypeError("compact must be bool")
    if compact:
        sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
        return
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _run_state(namespace: argparse.Namespace) -> int:
    response = _client(namespace).state(namespace.state_name)
    _write_json(response.json(), compact=namespace.compact)
    return _exit_code_for_status(response.status)


def _run_capabilities(namespace: argparse.Namespace) -> int:
    response = _client(namespace).capabilities()
    _write_json(response.json(), compact=namespace.compact)
    return _exit_code_for_status(response.status)


def _run_health(namespace: argparse.Namespace) -> int:
    response = _client(namespace).health()
    _write_json(response.json(), compact=namespace.compact)
    return _exit_code_for_status(response.status)


def _run_openapi(namespace: argparse.Namespace) -> int:
    response = _client(namespace).openapi()
    _write_json(response.json(), compact=namespace.compact)
    return _exit_code_for_status(response.status)


def _response_ok(response: Any) -> bool:
    return 200 <= int(response.status) < 300


def _response_payload(response: Any) -> dict[str, Any]:
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    return {"value": payload}


def _state_token_from_response(response: Any) -> str:
    if isinstance(response, ControlApiClientResponse):
        return LocalControlApiClient.state_token_from_response(response)
    raw_header_token = response.headers.get("X-State-Token", "")
    header_token = str(raw_header_token).strip()
    if header_token:
        return header_token.strip('"')
    payload = _response_payload(response)
    state = payload.get("state")
    if not isinstance(state, dict):
        return ""
    state_token = state.get("state_token")
    if not isinstance(state_token, str):
        return ""
    return state_token.strip().strip('"')


def _command_payload(namespace: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": _normalized_command_name(namespace.name),
        "value": _parse_cli_value(namespace.value),
    }
    if namespace.target:
        payload["target"] = namespace.target
    if namespace.detail:
        payload["detail"] = namespace.detail
    return payload


def _run_command(namespace: argparse.Namespace) -> int:
    response = _client(namespace).command(
        _command_payload(namespace),
        idempotency_key=namespace.idempotency_key,
        command_id=namespace.command_id,
        if_match=namespace.if_match,
    )
    _write_json(response.json(), compact=namespace.compact)
    return _exit_code_for_status(response.status)


def _event_request_kwargs(namespace: argparse.Namespace) -> dict[str, Any]:
    return {
        "limit": namespace.limit,
        "after": namespace.after,
        "resume": namespace.resume,
        "timeout": namespace.timeout,
        "heartbeat": namespace.heartbeat,
        "kinds": tuple(namespace.kind),
        "once": namespace.once,
    }


def _run_events(namespace: argparse.Namespace) -> int:
    response = _client(namespace).events(**_event_request_kwargs(namespace))
    return _write_event_response(response, compact=namespace.compact)


def _run_watch(namespace: argparse.Namespace) -> int:
    response = _client(namespace).events(**_event_request_kwargs(namespace))
    return _write_event_response(response, compact=True)


def _doctor_check(name: str, response: Any) -> dict[str, Any]:
    payload = _response_payload(response)
    return {
        "name": name,
        "ok": _response_ok(response),
        "status": int(response.status),
        "kind": payload.get("kind", ""),
    }


def _doctor_event_check(name: str, response: Any) -> dict[str, Any]:
    events = response.ndjson()
    return {
        "name": name,
        "ok": _response_ok(response),
        "status": int(response.status),
        "kind": "events",
        "event_count": len(events),
    }


def _token_or_global(namespace: argparse.Namespace, explicit_value: str) -> str:
    return explicit_value.strip() or namespace.token


def _safe_write_payload(namespace: argparse.Namespace) -> dict[str, Any]:
    return _command_payload(namespace)


def _safe_write_result(
    namespace: argparse.Namespace,
    *,
    token: str,
    state_endpoint: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    read_token = _token_or_global(namespace, getattr(namespace, "read_token", ""))
    read_client = _client_for_token(namespace, read_token or token)
    state_response = read_client.state(state_endpoint)
    state_token = _state_token_from_response(state_response)
    if not state_token:
        return EXIT_REQUEST_FAILED, {
            "ok": False,
            "kind": "safe-write",
            "error": "missing_state_token",
            "state_endpoint": state_endpoint,
        }
    command_response = _client_for_token(namespace, token).command(
        payload,
        idempotency_key=getattr(namespace, "idempotency_key", ""),
        command_id=getattr(namespace, "command_id", ""),
        if_match=state_token,
    )
    return _exit_code_for_status(command_response.status), {
        "ok": _response_ok(command_response),
        "kind": "safe-write",
        "state_endpoint": state_endpoint,
        "state_token": state_token,
        "command": payload,
        "response": _response_payload(command_response),
    }


def _run_safe_write(namespace: argparse.Namespace) -> int:
    exit_code, payload = _safe_write_result(
        namespace,
        token=namespace.token,
        state_endpoint=namespace.state_endpoint,
        payload=_safe_write_payload(namespace),
    )
    _write_json(payload, compact=namespace.compact)
    return exit_code


def _run_doctor(namespace: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []
    skipped: list[str] = []

    health_response = _client_for_token(namespace, "").health()
    checks.append(_doctor_check("health", health_response))

    read_token = _token_or_global(namespace, namespace.read_token)
    _append_doctor_read_checks(namespace, read_token, checks, skipped)

    control_token = _token_or_global(namespace, namespace.control_token)
    _append_doctor_safe_write_checks(namespace, read_token, control_token, checks, skipped)

    failed = [check for check in checks if not bool(check["ok"])]
    payload = _doctor_payload(checks, skipped, failed)
    _write_json(payload, compact=namespace.compact)
    return EXIT_OK if not failed else EXIT_REQUEST_FAILED


def _append_doctor_read_checks(
    namespace: argparse.Namespace,
    read_token: str,
    checks: list[dict[str, Any]],
    skipped: list[str],
) -> None:
    if not read_token:
        skipped.append("authenticated read checks skipped: no read token provided")
        return
    read_client = _client_for_token(namespace, read_token)
    checks.extend(
        [
            _doctor_check("capabilities", read_client.capabilities()),
            _doctor_check("state.summary", read_client.state("summary")),
            _doctor_check("state.health", read_client.state("health")),
            _doctor_event_check(
                "events.once",
                read_client.events(kinds=("command",), once=True, limit=10, timeout=2.0, heartbeat=0.5),
            ),
        ]
    )


def _append_doctor_safe_write_checks(
    namespace: argparse.Namespace,
    read_token: str,
    control_token: str,
    checks: list[dict[str, Any]],
    skipped: list[str],
) -> None:
    if not namespace.safe_write:
        return
    if not control_token:
        skipped.append("safe write skipped: no control token provided")
        return
    control_client = _client_for_token(namespace, control_token)
    operational_response = control_client.state("operational")
    checks.append(_doctor_check("state.operational", operational_response))
    checks.append(
        _doctor_safe_write_check(
            namespace,
            read_token,
            control_token,
            operational_response,
        )
    )


def _doctor_safe_write_check(
    namespace: argparse.Namespace,
    read_token: str,
    control_token: str,
    operational_response: Any,
) -> dict[str, Any]:
    safe_namespace = _doctor_safe_write_namespace(namespace, read_token, operational_response)
    exit_code, safe_payload = _safe_write_result(
        safe_namespace,
        token=control_token,
        state_endpoint="health",
        payload=_safe_write_payload(safe_namespace),
    )
    return {
        "name": "safe-write.set-mode",
        "ok": exit_code == EXIT_OK,
        "status": 200 if exit_code == EXIT_OK else 409,
        "kind": _safe_payload_kind(safe_payload),
    }


def _safe_payload_kind(payload: dict[str, Any]) -> str:
    kind = payload.get("kind")
    if isinstance(kind, str) and kind:
        return kind
    return "safe-write"


def _doctor_safe_write_namespace(
    namespace: argparse.Namespace,
    read_token: str,
    operational_response: Any,
) -> argparse.Namespace:
    operational_payload = _response_payload(operational_response)
    operational_state = operational_payload.get("state")
    mode_value = operational_state.get("mode", 0) if isinstance(operational_state, dict) else 0
    safe_namespace_values = dict(vars(namespace))
    safe_namespace_values.update(
        {
            "name": "set-mode",
            "value": str(mode_value),
            "target": "",
            "detail": "doctor-safe-write",
            "command_id": "doctor-safe-write",
            "idempotency_key": "doctor-safe-write",
            "read_token": read_token,
            "state_endpoint": "health",
        }
    )
    return argparse.Namespace(**safe_namespace_values)


def _doctor_payload(
    checks: list[dict[str, Any]],
    skipped: list[str],
    failed: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "ok": not failed,
        "kind": "doctor",
        "checks": checks,
        "skipped": skipped,
        "summary": {
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "skipped": len(skipped),
        },
    }


def _write_event_response(response: Any, *, compact: bool) -> int:
    if not isinstance(compact, bool):
        raise TypeError("compact must be bool")
    if compact:
        _write_stream_body(response.body)
    else:
        _write_json(response.ndjson(), compact=False)
    return _exit_code_for_status(response.status)


def _exit_code_for_status(status: int) -> int:
    return EXIT_OK if 200 <= status < 300 else EXIT_REQUEST_FAILED


def _write_stream_body(body: str) -> None:
    sys.stdout.write(body)
    if body and not body.endswith("\n"):
        sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(list(argv) if argv is not None else None)
    handlers = {
        "state": _run_state,
        "capabilities": _run_capabilities,
        "health": _run_health,
        "openapi": _run_openapi,
        "doctor": _run_doctor,
        "command": _run_command,
        "events": _run_events,
        "watch": _run_watch,
        "safe-write": _run_safe_write,
    }
    handler = handlers.get(namespace.subcommand)
    if handler is None:
        raise SystemExit(EXIT_USAGE)
    return handler(namespace)


if __name__ == "__main__":
    raise SystemExit(main())
