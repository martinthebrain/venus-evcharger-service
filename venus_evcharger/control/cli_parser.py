# SPDX-License-Identifier: GPL-3.0-or-later
"""Argument parser construction for the local Control API CLI."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser for the local Control and State API client."""
    parser = argparse.ArgumentParser(description="Small local client for the Venus EV charger Control API.")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Base URL for the local HTTP API.")
    parser.add_argument("--unix-socket", default="", help="Optional unix socket path to use instead of TCP.")
    parser.add_argument("--token", default="", help="Bearer token for read/control access.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Request timeout in seconds.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of pretty JSON.")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    _add_state_parser(subparsers)
    capabilities_parser = subparsers.add_parser("capabilities", help="Read the capabilities payload.")
    capabilities_parser.set_defaults(state_name="")
    subparsers.add_parser("health", help="Read the local API health payload.")
    subparsers.add_parser("openapi", help="Read the OpenAPI 3.1 description.")
    _add_doctor_parser(subparsers)
    _add_command_parser(subparsers)
    _add_events_parser(subparsers, "events", "Read one NDJSON event stream snapshot.", timeout=2.0, heartbeat=0.5, limit=20)
    _add_events_parser(subparsers, "watch", "Follow the event stream with ergonomic defaults.", timeout=30.0, heartbeat=1.0, limit=50)
    _add_safe_write_parser(subparsers)
    return parser


def _add_state_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    state_parser = subparsers.add_parser("state", help="Read one normalized state payload.")
    state_parser.add_argument(
        "state_name",
        choices=(
            "summary",
            "automation",
            "victron-bias-recommendation",
            "runtime",
            "operational",
            "dbus-diagnostics",
            "topology",
            "update",
            "health",
            "healthz",
            "version",
            "build",
            "contracts",
            "config-effective",
        ),
    )


def _add_doctor_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor_parser = subparsers.add_parser("doctor", help="Run a small local API/CLI self-test.")
    doctor_parser.add_argument("--read-token", default="", help="Optional explicit read token for doctor checks.")
    doctor_parser.add_argument(
        "--control-token",
        default="",
        help="Optional explicit control token for the safe-write doctor step.",
    )
    doctor_parser.add_argument(
        "--safe-write",
        action="store_true",
        help="Also perform one optimistic-concurrency safe write using the current mode value.",
    )


def _add_command_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command_parser = subparsers.add_parser("command", help="Send one canonical control command.")
    _add_command_arguments(command_parser)


def _add_safe_write_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    safe_write_parser = subparsers.add_parser(
        "safe-write",
        help="Fetch the current state token and send one command with If-Match.",
    )
    _add_command_arguments(safe_write_parser)
    safe_write_parser.add_argument(
        "--state-endpoint",
        choices=("automation", "health", "operational"),
        default="automation",
        help="State endpoint used to fetch the current optimistic concurrency token.",
    )
    safe_write_parser.add_argument(
        "--read-token",
        default="",
        help="Optional explicit read token used to fetch the state token before writing.",
    )


def _add_command_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", help="Canonical command name, for example set-mode.")
    parser.add_argument("value", help="Command value. JSON scalars such as 1, 12.5, true are accepted.")
    parser.add_argument("--path", default="", help="Explicit write path when the command family requires it.")
    parser.add_argument("--detail", default="", help="Optional detail string carried with the command.")
    parser.add_argument("--command-id", default="", help="Optional client-supplied command id.")
    parser.add_argument("--idempotency-key", default="", help="Optional replay-safe idempotency key.")
    if parser.prog.endswith(" command"):
        parser.add_argument("--if-match", default="", help="Optional optimistic concurrency token.")


def _add_events_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    *,
    timeout: float,
    heartbeat: float,
    limit: int,
) -> None:
    events_parser = subparsers.add_parser(name, help=help_text)
    events_parser.add_argument("--limit", type=int, default=limit)
    events_parser.add_argument("--after", type=int, default=None)
    events_parser.add_argument("--resume", type=int, default=None)
    events_parser.add_argument("--timeout", type=float, default=timeout)
    events_parser.add_argument("--heartbeat", type=float, default=heartbeat)
    events_parser.add_argument("--kind", action="append", default=[], help="Optional event kind filter.")
    events_parser.add_argument("--once", action="store_true")
