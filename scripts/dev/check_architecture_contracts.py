#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check narrow architecture contracts that are easy to regress silently."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from architecture_command_mailbox_contracts import check_command_mailbox_contracts
from architecture_gateway_operation_contracts import check_gateway_operation_contracts
from architecture_gateway_read_contracts import check_gateway_read_contracts
from architecture_suppression_contracts import check_suppression_contracts

REPO = Path(__file__).resolve().parents[2]

FORBIDDEN_SUBSTRINGS = {
    "DBUS_GATEWAY.md": (
        "GatewayClient.request_read_key",
        "GatewayClient.request_raw_value",
        "gateway_read_value",
        "kind=set_value",
    ),
    "scripts/dev/pi_gateway_release_gate_remote.py": (
        "-m py_compile",
        "-m compileall",
    ),
    "scripts/dev/pi_safety_invariants_gate.py": (
        "-m py_compile",
        "-m compileall",
    ),
    "venus_evcharger/app/bootstrap_support.py": ("setup_dbus_mainloop",),
    "venus_evcharger/bootstrap/controller.py": (
        "_setup_dbus_mainloop",
        "_dbus_service_owned_by_current_process",
    ),
}

FORBIDDEN_FILE_PATTERNS = {
    "venus_evcharger/controllers": {
        "retired state-controller inheritance artifacts": re.compile(
            r"\b(?:_StateValidation|_StateSummary|_StateRuntime(?:Restore(?:VictronEss)?|Overrides|Snapshot|Normalize)?|"
            r"_StateRestore)\b|venus_evcharger\.controllers\.(?:state_restore_support|state_runtime)\b"
        ),
    },
    "venus_evcharger": {
        "retired backend compatibility APIs": re.compile(
            r"\b(?:compat_legacy_backend_view|config_service_compat|"
            r"runtime_summary_from_legacy_service_attrs|service_has_legacy_backend_attrs)\b"
        ),
    },
    "venus_evcharger/dbus_adapter/write": {
        "retired write-scheduler inheritance roles": re.compile(
            r"\bDbusWriteScheduler(?:Core|Publish|Semantic|Health)\b"
        ),
    },
    "tests": {
        "legacy private port calls": re.compile(
            r"\bport\._(?:"
            r"get_dbus_value|clear_auto_samples|queue_relay_command|publish_dbus_path|"
            r"get_worker_snapshot|update_worker_snapshot|mode_uses_auto_logic"
            r")\("
        ),
    },
    "venus_evcharger/controllers/write_support.py": {
        "write support private port calls": re.compile(
            r"\bsvc\._(?:"
            r"clear_auto_samples|queue_relay_command|publish_dbus_path|"
            r"get_worker_snapshot|update_worker_snapshot|mode_uses_auto_logic"
            r")\("
        ),
    },
    "venus_evcharger/auto/logic_samples.py": {
        "auto logic private port calls": re.compile(r"\b(?:svc|self\.service)\._clear_auto_samples\("),
    },
    "venus_evcharger/auto": {
        "auto workflow private port calls": re.compile(
            r"\b(?:svc|self\.service)\._(?:"
            r"get_available_surplus_watts|add_auto_sample|average_auto_metric|"
            r"is_within_auto_daytime_window|save_runtime_state|peek_pending_relay_command|"
            r"write_auto_audit_event"
            r")\("
        ),
    },
}

REQUIRED_GATEWAY_CONTRACT_SYMBOLS = {
    "venus_evcharger/ipc/energy_binary.py": frozenset(
        {
            "_MAX_PAYLOAD_BYTES",
            "_read_bounded_payload",
            "decode_energy_inputs",
            "read",
        }
    ),
    "venus_evcharger/ipc/energy_snapshots.py": frozenset(
        {"EnergyInputsSnapshot", "__post_init__", "timestamp_not_future"}
    ),
    "venus_evcharger/ipc/deadline.py": frozenset(
        {
            "TRANSIENT_PUBLICATION_DEADLINE_SECONDS",
            "command_deadline_expired",
            "normalized_transient_deadline",
            "remaining_transient_ttl",
            "valid_deadline_anchor",
        }
    ),
    "venus_evcharger/ipc/publication_order.py": frozenset(
        {
            "PUBLICATION_FIELD_ORDERS_FIELD",
            "PublicationOrderHistory",
            "PublicationOrderSequence",
            "claim_durable",
            "claim_fast",
            "publication_field_orders",
        }
    ),
    "venus_evcharger/ipc/fast_publication.py": frozenset(
        {
            "FastPublicationQueue",
            "pop_next",
            "prepare_durable",
            "publication_field_orders",
            "remaining_transient_ttl",
            "requeue",
        }
    ),
    "venus_evcharger/dbus_adapter/write/core.py": frozenset(
        {
            "_process_fast_publish_burst",
            "_urgent_durable_ready",
            "command_deadline_expired",
            "process_local_publish_burst",
        }
    ),
    "venus_evcharger/ipc/command_mailbox.py": frozenset(
        {
            "MAILBOX_REVISION_FIELD",
            "MailboxLockTimeout",
            "_locked",
            "remove_if_current",
        }
    ),
}


@dataclass(frozen=True)
class _AllowedMultipleInheritance:
    bases: tuple[str, ...]
    reason: str


_PROTOCOL_COMPOSITION = "Protocol-only composition of explicit capabilities."


def _allowed(*bases: str, reason: str = _PROTOCOL_COMPOSITION) -> _AllowedMultipleInheritance:
    return _AllowedMultipleInheritance(bases=bases, reason=reason)


ALLOWED_MULTIPLE_INHERITANCE = {
    "venus_evcharger/dbus_adapter/process/protocols/context.py": {
        "DbusAdapterProcessContext": _allowed(
            "DbusAdapterRuntimeContext", "DbusAdapterSocketContext", "DbusAdapterLoopContext",
            "DbusAdapterIoContext", "DbusAdapterHealthContext", "DbusAdapterPublicationContext",
            "DbusAdapterIntrospectionContext", "DbusAdapterIntrospectionSnapshotContext", "Protocol"
        )
    },
    "venus_evcharger/control/http_api.py": {
        "_ThreadingLocalControlUnixHttpServer": _allowed(
            "socketserver.ThreadingMixIn", "socketserver.UnixStreamServer",
            reason="stdlib server composition for threaded Unix-domain HTTP."
        )
    },
    "venus_evcharger/update/offline_publish.py": {
        "OfflineService": _allowed("RelayTelemetryService", "Protocol"),
    },
    "venus_evcharger/update/relay_ports.py": {
        "PhaseSwitchCombinedRuntimePort": _allowed(
            "PhaseSwitchRuntimePort", "RelayTelemetryRuntimePort", "Protocol"
        ),
        "PhaseSwitchServicePort": _allowed("RelayTelemetryService", "Protocol"),
    },
    "venus_evcharger/update/relay_status_publish.py": {
        "RelayStatusRuntimePort": _allowed(
            "StatusRuntimePort", "ChargerRuntimePort", "RelayTelemetryRuntimePort", "Protocol"
        ),
        "RelayStatusService": _allowed("ChargerControlService", "RelayTelemetryService", "Protocol"),
    },
    "venus_evcharger/update/runtime_cycle_contracts.py": {
        "UpdateCycleAutoPort": _allowed(
            "StateAutoPort", "PhaseSwitchAutoPort", "OfflineAutoPort", "Protocol"
        ),
        "UpdateCycleRuntimePort": _allowed(
            "StateRuntimePort", "PhaseSwitchRuntimePort", "ChargerRuntimePort",
            "RelayTelemetryRuntimePort", "StatusRuntimePort", "Protocol"
        ),
        "UpdateCycleStatePort": _allowed(
            "StatePublishPort", "PhaseSwitchStatePort", "OfflineStatePort", "Protocol"
        ),
        "UpdateCycleReadbackPort": _allowed(
            "StateReadbackPort", "PhaseSwitchReadbackPort", "StatusReadbackPort", "Protocol"
        ),
        "UpdateCycleServicePort": _allowed(
            "UpdateStateService", "InputCacheService", "PmSnapshotService", "OfflineService",
            "PhaseSwitchServicePort", "RelayStatusService", "ChargerControlService",
            "ChargerHealthService", "_LearningRuntimeService", "RuntimeWarningServicePort", "Protocol"
        ),
    },
}

_EXPECTED_LINEAR_CLASSES = (
    ("venus_evcharger/controllers/state.py", "ServiceStateController"),
    ("venus_evcharger/dbus_adapter/process/adapter.py", "DbusAdapter"),
    ("venus_evcharger/dbus_adapter/process/diagnostics.py", "DbusAdapterDiagnostics"),
    ("venus_evcharger/dbus_adapter/process/health.py", "DbusAdapterHealth"),
    ("venus_evcharger/dbus_adapter/process/introspection.py", "DbusAdapterIntrospection"),
    ("venus_evcharger/dbus_adapter/process/introspection_snapshot.py", "DbusAdapterIntrospectionSnapshot"),
    ("venus_evcharger/dbus_adapter/process/io.py", "DbusAdapterIo"),
    ("venus_evcharger/dbus_adapter/process/loop.py", "DbusAdapterLoop"),
    ("venus_evcharger/dbus_adapter/process/publication.py", "DbusAdapterPublication"),
    ("venus_evcharger/dbus_adapter/process/runtime.py", "DbusAdapterRuntime"),
    ("venus_evcharger/dbus_adapter/process/socket.py", "DbusAdapterSocket"),
    ("venus_evcharger/dbus_adapter/write/core.py", "WriteCommandQueue"),
    ("venus_evcharger/dbus_adapter/write/health.py", "WriteSchedulerHealthTracker"),
    ("venus_evcharger/dbus_adapter/write/publish.py", "GatewayPublicationExecutor"),
    ("venus_evcharger/dbus_adapter/write/scheduler.py", "DbusWriteScheduler"),
    ("venus_evcharger/dbus_adapter/write/semantic.py", "SemanticWriteExecutor"),
    ("venus_evcharger/update/controller.py", "UpdateCycleController"),
    ("venus_evcharger/service/controller_owner.py", "ServiceControllerOwner"),
    ("venus_evcharger/service/auto_facade.py", "ServiceAutoFacade"),
    ("venus_evcharger/service/runtime_facade.py", "ServiceRuntimeFacade"),
    ("venus_evcharger/service/state_facade.py", "ServiceStateFacade"),
    ("venus_evcharger/service/update_facade.py", "ServiceUpdateFacade"),
    ("venus_evcharger/service/control.py", "ServiceControlFacade"),
    ("venus_evcharger_service.py", "ShellyWallboxService"),
    ("venus_evcharger_auto_input_helper.py", "AutoInputHelper"),
)
EXPECTED_CLASS_BASES: dict[str, dict[str, tuple[str, ...]]] = {
    path: {class_name: ()} for path, class_name in _EXPECTED_LINEAR_CLASSES
}

RETIRED_STATE_MODULES = (
    "venus_evcharger/controllers/state_restore_support.py",
    "venus_evcharger/controllers/state_runtime.py",
)

GATEWAY_SURFACE_IMPORT_PATTERN = re.compile(r"\b(?:from|import)\s+venus_evcharger\.dbus_gateway_surface\b")


def _repo_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _module_symbols(path: Path) -> set[str]:
    tree = ast.parse(_repo_text(path), filename=str(path))
    return {
        node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _check_required_gateway_contracts() -> list[str]:
    return [
        f"{relative}: required gateway contract symbol {symbol!r} is missing"
        for relative, required in REQUIRED_GATEWAY_CONTRACT_SYMBOLS.items()
        for symbol in sorted(required - _module_symbols(REPO / relative))
    ]


def _check_forbidden_substrings() -> list[str]:
    failures: list[str] = []
    for relative_path, forbidden_values in FORBIDDEN_SUBSTRINGS.items():
        text = _repo_text(REPO / relative_path)
        failures.extend(
            f"{relative_path}: contains forbidden compatibility marker {value!r}"
            for value in forbidden_values
            if value in text
        )
    return failures


def _paths_for(relative_root: str) -> list[Path]:
    root = REPO / relative_root
    return sorted(root.rglob("*.py")) if root.is_dir() else [root]


def _pattern_failures_for(path: Path, label: str, pattern: re.Pattern[str]) -> list[str]:
    text = _repo_text(path)
    relative_path = path.relative_to(REPO)
    return [
        f"{relative_path}:{_line_number(text, match.start())}: {label}: {match.group(0)}"
        for match in pattern.finditer(text)
    ]


def _check_forbidden_patterns() -> list[str]:
    failures: list[str] = []
    for relative_root, patterns in FORBIDDEN_FILE_PATTERNS.items():
        for path in _paths_for(relative_root):
            for label, pattern in patterns.items():
                failures.extend(_pattern_failures_for(path, label, pattern))
    return failures


def _check_retired_state_modules() -> list[str]:
    return [
        f"{relative_path}: retired state-controller module must remain absent"
        for relative_path in RETIRED_STATE_MODULES
        if (REPO / relative_path).exists()
    ]


def _class_base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        prefix = _class_base_name(base.value)
        return f"{prefix}.{base.attr}" if prefix else base.attr
    return ast.unparse(base)


def _multiple_inheritance_classes(path: Path) -> list[tuple[int, str, tuple[str, ...]]]:
    tree = ast.parse(_repo_text(path), filename=str(path))
    return [
        (node.lineno, node.name, tuple(_class_base_name(base) for base in node.bases))
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and len(node.bases) > 1
    ]


def _top_level_class_bases(path: Path) -> dict[str, tuple[int, tuple[str, ...]]]:
    tree = ast.parse(_repo_text(path), filename=str(path))
    classes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = (node.lineno, tuple(_class_base_name(base) for base in node.bases))
    return classes


def _check_expected_class_bases() -> list[str]:
    failures: list[str] = []
    for relative_path, expected_classes in EXPECTED_CLASS_BASES.items():
        classes = _top_level_class_bases(REPO / relative_path)
        for class_name, expected_bases in expected_classes.items():
            if class_name not in classes:
                failures.append(f"{relative_path}: expected class {class_name} is missing")
                continue
            line_number, actual_bases = classes[class_name]
            if actual_bases != expected_bases:
                failures.append(
                    f"{relative_path}:{line_number}: {class_name} direct bases changed "
                    f"from {expected_bases!r} to {actual_bases!r}"
                )
    return failures


def _multiple_inheritance_contract_failure(
    relative_path: str,
    line_number: int,
    class_name: str,
    bases: tuple[str, ...],
) -> str | None:
    allowed = ALLOWED_MULTIPLE_INHERITANCE.get(relative_path, {}).get(class_name)
    if allowed is None:
        return (
            f"{relative_path}:{line_number}: unexpected multiple inheritance "
            f"on {class_name}: {', '.join(bases)}"
        )
    if bases != allowed.bases:
        return (
            f"{relative_path}:{line_number}: allowed multiple inheritance "
            f"on {class_name} changed from {allowed.bases!r} to {bases!r}"
        )
    return None


def _scan_multiple_inheritance_contracts() -> tuple[list[str], set[tuple[str, str]]]:
    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted((REPO / "venus_evcharger").rglob("*.py")):
        relative_path = str(path.relative_to(REPO))
        for line_number, class_name, bases in _multiple_inheritance_classes(path):
            failure = _multiple_inheritance_contract_failure(relative_path, line_number, class_name, bases)
            if failure:
                failures.append(failure)
            else:
                seen.add((relative_path, class_name))
    return failures, seen


def _missing_allowed_multiple_inheritance(seen: set[tuple[str, str]]) -> list[str]:
    return [
        f"{relative_path}: allowed multiple inheritance class {class_name} is missing"
        for relative_path, classes in ALLOWED_MULTIPLE_INHERITANCE.items()
        for class_name in classes
        if (relative_path, class_name) not in seen
    ]


def _check_multiple_inheritance_contract() -> list[str]:
    failures, seen = _scan_multiple_inheritance_contracts()
    failures.extend(_missing_allowed_multiple_inheritance(seen))
    return failures


def _check_gateway_surface_boundary() -> list[str]:
    failures: list[str] = []
    for path in sorted((REPO / "venus_evcharger").rglob("*.py")):
        relative_path = str(path.relative_to(REPO))
        text = _repo_text(path)
        if "dbus_venus_surface" in text:
            failures.append(f"{relative_path}: legacy dbus_venus_surface import/name is forbidden")
        if GATEWAY_SURFACE_IMPORT_PATTERN.search(text) and not relative_path.startswith("venus_evcharger/dbus_gateway"):
            failures.append(f"{relative_path}: import Venus surface contracts through venus_evcharger.dbus_gateway")
    return failures


def _check_dbus_adapter_layout() -> list[str]:
    legacy_modules = sorted((REPO / "venus_evcharger").glob("dbus_adapter_*.py"))
    return [
        f"{path.relative_to(REPO)}: fragmented adapter modules belong under venus_evcharger/dbus_adapter/"
        for path in legacy_modules
    ]


def main() -> int:
    failures = [
        *_check_forbidden_substrings(),
        *_check_forbidden_patterns(),
        *_check_retired_state_modules(),
        *_check_expected_class_bases(),
        *_check_multiple_inheritance_contract(),
        *check_suppression_contracts(REPO),
        *_check_gateway_surface_boundary(),
        *_check_dbus_adapter_layout(),
        *_check_required_gateway_contracts(),
        *check_command_mailbox_contracts(REPO),
        *check_gateway_operation_contracts(REPO),
        *check_gateway_read_contracts(REPO),
    ]
    if failures:
        print("Architecture contract violations found:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("Architecture contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
