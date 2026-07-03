#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a focused mutmut audit for gateway and runtime-safety logic.

The audit is intentionally optional and slow.  It mutates one target module at a
time, writes a full log per target, and creates a compact JSON summary that can
be inspected after a long run.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import mutation_audit_support as audit_support

DEFAULT_TEST_SELECTION = (
    "tests/test_dbus_gateway_adapter_scheduler.py",
    "tests/test_dbus_gateway_primitives.py",
    "tests/test_core_dbus_backpressure.py",
    "tests/test_venus_evcharger_shared.py",
    "tests/test_venus_evcharger_dbus_inputs_controller.py",
    "tests/venus_evcharger_helpers_cases_primary.py",
    "tests/venus_evcharger_helpers_cases_quaternary.py",
    "tests/venus_evcharger_auto_input_helper_cases_basic.py",
    "tests/venus_evcharger_auto_input_helper_cases_sources.py",
    "tests/test_auto_logic_types.py",
    "tests/test_venus_evcharger_auto_policy.py",
    "tests/test_venus_evcharger_auto_controller.py",
    "tests/venus_evcharger_auto_controller_cases_primary.py",
    "tests/venus_evcharger_auto_controller_cases_recovery.py",
    "tests/test_venus_evcharger_backend_shelly_meter.py",
    "tests/test_venus_evcharger_backend_shelly_support.py",
    "tests/test_venus_evcharger_backend_switch.py",
    "tests/test_venus_evcharger_shelly_io_controller.py",
    "tests/venus_evcharger_shelly_io_controller_cases_primary.py",
    "tests/venus_evcharger_shelly_io_controller_cases_secondary.py",
    "tests/venus_evcharger_shelly_io_controller_cases_tertiary.py",
    "tests/venus_evcharger_shelly_io_controller_cases_quaternary.py",
    "tests/test_venus_evcharger_update_cycle_controller.py",
    "tests/venus_evcharger_update_cycle_cases_quaternary_learning.py",
    "tests/venus_evcharger_update_cycle_cases_quaternary_runtime.py",
    "tests/venus_evcharger_update_cycle_cases_quaternary_victron_adaptive.py",
    "tests/venus_evcharger_update_cycle_cases_quaternary_victron_core.py",
    "tests/venus_evcharger_update_cycle_controller_cases_primary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_secondary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_tertiary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_quaternary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_quinary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_senary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_septenary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_octonary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_nonary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_denary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_undenary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_duodenary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_trecenary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_quattuordenary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_quindenary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_sexdecenary.py",
    "tests/venus_evcharger_update_cycle_controller_cases_septendecenary.py",
)
DEFAULT_TARGETS = (
    "venus_evcharger/dbus_gateway_policy.py",
    "venus_evcharger/dbus_gateway_commands.py",
    "venus_evcharger/dbus_gateway_cache.py",
    "venus_evcharger/dbus_gateway_core.py",
    "venus_evcharger/dbus_gateway_client.py",
    "venus_evcharger/dbus_gateway_latency.py",
    "venus_evcharger/dbus_adapter_write_health.py",
    "venus_evcharger/dbus_adapter_write_core.py",
    "venus_evcharger/dbus_adapter_write_publish.py",
    "venus_evcharger/dbus_adapter_read.py",
    "venus_evcharger/dbus_adapter_process_health.py",
    "venus_evcharger/dbus_adapter_process_loop.py",
    "venus_evcharger/core/dbus_backpressure.py",
    "venus_evcharger/auto/policy.py",
    "venus_evcharger/auto/logic_learning.py",
    "venus_evcharger/auto/logic_decisions.py",
    "venus_evcharger/auto/logic_gates_runtime.py",
    "venus_evcharger/backend/shelly_support.py",
    "venus_evcharger/backend/shelly_io_runtime.py",
    "venus_evcharger/backend/shelly_meter.py",
    "venus_evcharger/update/offline_publish.py",
    "venus_evcharger/update/learning_runtime.py",
    "venus_evcharger/update/runtime_cycle.py",
    "venus_evcharger/update/relay_charger_current_targets.py",
    "venus_evcharger/update/relay_charger_current.py",
    "venus_evcharger/update/relay_phase_decision.py",
    "venus_evcharger/update/relay_phase_switch_policy.py",
    "venus_evcharger/update/relay_phase_switch_runtime.py",
    "venus_evcharger/update/relay_phase_switch_runtime_recovery.py",
    "venus_evcharger/update/relay_status_publish.py",
    "venus_evcharger/update/learning_signature.py",
    "venus_evcharger/update/relay_phase_switch_mismatch.py",
)
MUTMUT_CACHE, MUTMUT_WORKTREE = ".mutmut-cache", "mutants"
RESULT_WORDS = audit_support.RESULT_WORDS
TIMEOUT_RETURNCODE, OK_STATUSES = audit_support.TIMEOUT_RETURNCODE, audit_support.OK_STATUSES

_parse_counts = audit_support.parse_counts
_survivor_names = audit_support.survivor_names
_mutant_names = audit_support.mutant_names
_target_status = audit_support.target_status
_no_mutant_test_mapping = audit_support.no_mutant_test_mapping
_status_from_counts = audit_support.status_from_counts
_slug = audit_support.slug


@dataclass
class TargetResult:
    path: str
    status: str
    duration_s: float
    run_returncode: int
    results_returncode: int
    log_path: str
    results_path: str
    counts: dict[str, int]


def main(argv: list[str] | None = None) -> int:
    return _run_cli(_parse_args(argv))


def _run_cli(args: argparse.Namespace) -> int:
    selected = _selected_targets(args.targets)
    if args.list_targets:
        return _print_targets(selected)

    mutmut = _mutmut_command()
    if mutmut is None:
        print("mutmut is required. Install it with: python3 -m pip install mutmut", file=sys.stderr)
        return 2

    repo = _repo_dir()
    out_dir = _output_dir(repo, args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with audit_support.mutmut_audit_lock(repo):
        results = [
            _run_target(repo=repo, out_dir=out_dir, mutmut=mutmut, target=target, args=args) for target in selected
        ]
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True) + "\n")
    _print_summary(results, summary_path)
    return _exit_code(results, no_fail=args.no_fail)


def _print_targets(targets: list[audit_support.MutationTarget]) -> int:
    for target in targets:
        print(target.path)
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="Target module path(s). Defaults to the curated gateway list.")
    parser.add_argument("--list-targets", action="store_true", help="Print selected targets and exit.")
    parser.add_argument("--out-dir", help="Output directory for logs. Defaults to build/mutation-audit/<timestamp>.")
    parser.add_argument("--timeout-s", type=float, default=1800.0, help="Timeout per mutated module.")
    parser.add_argument("--reuse-cache", action="store_true", help="Do not clear .mutmut-cache between target modules.")
    parser.add_argument(
        "--verify-survivors",
        action="store_true",
        help="Re-apply surviving mutants and run the selected tests in a clean subprocess.",
    )
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 after writing logs and summary.")
    return parser.parse_args(argv)


def _repo_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _mutmut_command() -> list[str] | None:
    local_python = _repo_dir() / ".venv-mutmut" / "bin" / "python"
    if local_python.exists():
        return [str(local_python), "-m", "mutmut"]
    executable = shutil.which("mutmut")
    if executable is not None:
        return [executable]
    if _current_python_has_mutmut():
        return [sys.executable, "-m", "mutmut"]
    return None


def _current_python_has_mutmut() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import mutmut"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _selected_targets(paths: list[str]) -> list[audit_support.MutationTarget]:
    selected = paths or list(DEFAULT_TARGETS)
    return [audit_support.MutationTarget(path=path) for path in selected]


def _output_dir(repo: Path, requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return repo / "build" / "mutation-audit" / timestamp


def _run_target(
    *,
    repo: Path,
    out_dir: Path,
    mutmut: list[str],
    target: audit_support.MutationTarget,
    args: argparse.Namespace,
) -> TargetResult:
    started = time.monotonic()
    slug = _slug(target.path)
    artifacts = audit_support.TargetArtifacts(
        log_path=out_dir / f"{slug}.log",
        results_path=out_dir / f"{slug}.results.txt",
    )
    command = audit_support.MutationCommandContext(repo=repo, mutmut=mutmut)
    not_applicable_reason = audit_support.not_applicable_mutation_target(repo, target.path)
    if not_applicable_reason is not None:
        return _not_applicable_result(
            target=target,
            started=started,
            log_path=artifacts.log_path,
            results_path=artifacts.results_path,
            reason=not_applicable_reason,
        )
    _clear_mutmut_worktree(repo, reuse_cache=args.reuse_cache)

    run = _run_mutmut_for_target(
        command=command,
        target=target,
        artifacts=artifacts,
        timeout_s=args.timeout_s,
    )
    counts = _target_counts(
        command=command,
        target=target,
        run=run,
        artifacts=artifacts,
        verify_survivors=args.verify_survivors,
    )
    duration = time.monotonic() - started
    status = _target_status(
        run.run_result.returncode,
        run.results_result.returncode,
        counts,
        log_text=run.log_text,
    )
    return TargetResult(
        path=target.path,
        status=status,
        duration_s=round(duration, 3),
        run_returncode=run.run_result.returncode,
        results_returncode=run.results_result.returncode,
        log_path=str(artifacts.log_path),
        results_path=str(artifacts.results_path),
        counts=counts,
    )


def _not_applicable_result(
    *,
    target: audit_support.MutationTarget,
    started: float,
    log_path: Path,
    results_path: Path,
    reason: str,
) -> TargetResult:
    counts = dict.fromkeys(RESULT_WORDS, 0)
    counts["skipped"] = 1
    _write_not_applicable_result(log_path, results_path, target.path, reason)
    return TargetResult(
        path=target.path,
        status="not_applicable",
        duration_s=round(time.monotonic() - started, 3),
        run_returncode=0,
        results_returncode=0,
        log_path=str(log_path),
        results_path=str(results_path),
        counts=counts,
    )


def _clear_mutmut_worktree(repo: Path, *, reuse_cache: bool) -> None:
    if reuse_cache:
        return
    shutil.rmtree(repo / MUTMUT_CACHE, ignore_errors=True)
    shutil.rmtree(repo / MUTMUT_WORKTREE, ignore_errors=True)


def _run_mutmut_for_target(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    artifacts: audit_support.TargetArtifacts,
    timeout_s: float,
) -> audit_support.TargetMutmutRun:
    with _mutmut_config_for_target(command.repo, target.path):
        run_result = _run_logged(
            [*command.mutmut, "run"],
            cwd=command.repo,
            log_path=artifacts.log_path,
            timeout_s=timeout_s,
        )
        results_result = _capture_results(
            mutmut=command.mutmut,
            cwd=command.repo,
            results_path=artifacts.results_path,
        )
    return audit_support.TargetMutmutRun(
        run_result=run_result,
        results_result=results_result,
        result_text=artifacts.results_path.read_text(errors="replace") if artifacts.results_path.exists() else "",
        log_text=artifacts.log_path.read_text(errors="replace") if artifacts.log_path.exists() else "",
    )


def _target_counts(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    verify_survivors: bool,
) -> dict[str, int]:
    counts = _parse_counts(run.result_text)
    if _run_has_no_test_mapping(run, counts):
        counts["no_tests"] += 1
    if verify_survivors:
        _verify_target_attention_counts(command=command, target=target, run=run, artifacts=artifacts, counts=counts)
    return counts


def _run_has_no_test_mapping(run: audit_support.TargetMutmutRun, counts: dict[str, int]) -> bool:
    return _no_mutant_test_mapping(
        run.run_result.returncode,
        run.results_result.returncode,
        counts,
        run.log_text,
    )


def _verify_target_attention_counts(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    counts: dict[str, int],
) -> None:
    if counts["survived"]:
        _verify_survived_mutants(command=command, target=target, run=run, artifacts=artifacts, counts=counts)
    if counts["no_tests"]:
        _verify_no_test_mutants(command=command, target=target, run=run, artifacts=artifacts, counts=counts)


def _verify_survived_mutants(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    counts: dict[str, int],
) -> None:
    verified = audit_support.verify_survivors(
        repo=command.repo,
        mutmut=command.mutmut,
        target_path=target.path,
        survivor_names=_survivor_names(run.result_text),
        log_path=artifacts.log_path,
        test_selection=DEFAULT_TEST_SELECTION,
        configure_target=_mutmut_config_for_target,
    )
    _move_verified_mutants_to_killed(counts, "survived", verified)


def _verify_no_test_mutants(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    counts: dict[str, int],
) -> None:
    verified = audit_support.verify_mutants(
        repo=command.repo,
        mutmut=command.mutmut,
        target_path=target.path,
        mutant_names=_mutant_names(run.result_text, "no tests"),
        log_path=artifacts.log_path,
        test_selection=DEFAULT_TEST_SELECTION,
        configure_target=_mutmut_config_for_target,
        status_label="no-tests",
    )
    _move_verified_mutants_to_killed(counts, "no_tests", verified)


def _move_verified_mutants_to_killed(counts: dict[str, int], source_status: str, verified: int) -> None:
    counts[source_status] = max(0, counts[source_status] - verified)
    counts["killed"] += verified


@contextlib.contextmanager
def _mutmut_config_for_target(repo: Path, target_path: str):
    pyproject = repo / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(_pyproject_with_mutmut_config(original, target_path), encoding="utf-8")
    try:
        yield
    finally:
        pyproject.write_text(original, encoding="utf-8")


def _pyproject_with_mutmut_config(original: str, target_path: str) -> str:
    cleaned = _strip_tool_mutmut_section(original).rstrip()
    return f"{cleaned}\n\n{_mutmut_config_toml(target_path)}"


def _strip_tool_mutmut_section(content: str) -> str:
    return re.sub(r"(?ms)^\[tool\.mutmut\]\n.*?(?=^\[|\Z)", "", content).strip() + "\n"


def _mutmut_config_toml(target_path: str) -> str:
    lines = [
        "[tool.mutmut]",
        'source_paths = ["venus_evcharger"]',
        f'only_mutate = ["{target_path}"]',
        "also_copy = [",
        '    "venus_evcharger_auto_input_helper.py",',
        '    "venus_evcharger_dbus_adapter.py",',
        '    "venus_evcharger_service.py",',
        "]",
        'pytest_add_cli_args = ["-k", "not socket"]',
        "pytest_add_cli_args_test_selection = [",
    ]
    lines.extend(f'    "{path}",' for path in DEFAULT_TEST_SELECTION)
    return "\n".join([*lines, "]", ""])


def _run_logged(command: list[str], *, cwd: Path, log_path: Path, timeout_s: float) -> subprocess.CompletedProcess[str]:
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {audit_support.shellish(command)}\n\n")
        log.flush()
        try:
            process = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            log.write(f"\nTIMEOUT after {timeout_s:.1f}s\n{error}\n")
            return subprocess.CompletedProcess(command, TIMEOUT_RETURNCODE)
    return process


def _capture_results(*, mutmut: list[str], cwd: Path, results_path: Path) -> subprocess.CompletedProcess[str]:
    command = [*mutmut, "results", "--all", "true"]
    with results_path.open("w", encoding="utf-8") as output:
        output.write(f"$ {audit_support.shellish(command)}\n\n")
        output.flush()
        return subprocess.run(command, cwd=cwd, check=False, stdout=output, stderr=subprocess.STDOUT, text=True)


def _exit_code(results: list[TargetResult], *, no_fail: bool) -> int:
    return audit_support.exit_code([result.status for result in results], no_fail=no_fail)


def _print_summary(results: list[TargetResult], summary_path: Path) -> None:
    print(f"Mutation audit summary: {summary_path}")
    for result in results:
        counts = " ".join(f"{key}={value}" for key, value in sorted(result.counts.items()))
        print(f"- {result.status:15} {result.duration_s:8.1f}s {result.path} {counts}")


def _write_not_applicable_result(log_path: Path, results_path: Path, target_path: str, reason: str) -> None:
    audit_support.write_not_applicable_result(log_path, results_path, target_path, reason)


if __name__ == "__main__":
    raise SystemExit(main())
