#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a focused mutmut audit for gateway-critical logic.

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

DEFAULT_TEST_SELECTION = (
    "tests/test_dbus_gateway_adapter_scheduler.py",
    "tests/test_dbus_gateway_primitives.py",
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
)
MUTMUT_CACHE = ".mutmut-cache"
MUTMUT_WORKTREE = "mutants"
RESULT_WORDS = ("killed", "survived", "timeout", "suspicious", "skipped")
ATTENTION_WORDS = ("survived", "timeout", "suspicious")
TIMEOUT_RETURNCODE = 124


@dataclass(frozen=True)
class MutationTarget:
    path: str


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
    results = [
        _run_target(repo=repo, out_dir=out_dir, mutmut=mutmut, target=target, args=args) for target in selected
    ]
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True) + "\n")
    _print_summary(results, summary_path)
    return _exit_code(results, no_fail=args.no_fail)


def _print_targets(targets: list[MutationTarget]) -> int:
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


def _selected_targets(paths: list[str]) -> list[MutationTarget]:
    selected = paths or list(DEFAULT_TARGETS)
    return [MutationTarget(path=path) for path in selected]


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
    target: MutationTarget,
    args: argparse.Namespace,
) -> TargetResult:
    started = time.monotonic()
    slug = _slug(target.path)
    log_path = out_dir / f"{slug}.log"
    results_path = out_dir / f"{slug}.results.txt"
    if not args.reuse_cache:
        shutil.rmtree(repo / MUTMUT_CACHE, ignore_errors=True)
        shutil.rmtree(repo / MUTMUT_WORKTREE, ignore_errors=True)

    with _mutmut_config_for_target(repo, target.path):
        run_result = _run_logged([*mutmut, "run"], cwd=repo, log_path=log_path, timeout_s=args.timeout_s)
        results_result = _capture_results(mutmut=mutmut, cwd=repo, results_path=results_path)
    result_text = results_path.read_text(errors="replace") if results_path.exists() else ""
    log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
    counts = _parse_counts(result_text)
    duration = time.monotonic() - started
    status = _target_status(run_result.returncode, results_result.returncode, counts, log_text=log_text)
    return TargetResult(
        path=target.path,
        status=status,
        duration_s=round(duration, 3),
        run_returncode=run_result.returncode,
        results_returncode=results_result.returncode,
        log_path=str(log_path),
        results_path=str(results_path),
        counts=counts,
    )


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
        'also_copy = ["venus_evcharger_dbus_adapter.py"]',
        'pytest_add_cli_args = ["-k", "not socket"]',
        "pytest_add_cli_args_test_selection = [",
    ]
    lines.extend(f'    "{path}",' for path in DEFAULT_TEST_SELECTION)
    return "\n".join([*lines, "]", ""])


def _run_logged(command: list[str], *, cwd: Path, log_path: Path, timeout_s: float) -> subprocess.CompletedProcess[str]:
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {_shellish(command)}\n\n")
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
        output.write(f"$ {_shellish(command)}\n\n")
        output.flush()
        return subprocess.run(command, cwd=cwd, check=False, stdout=output, stderr=subprocess.STDOUT, text=True)


def _parse_counts(text: str) -> dict[str, int]:
    counts = dict.fromkeys(RESULT_WORDS, 0)
    for word in RESULT_WORDS:
        counts[word] += sum(int(value) for value in re.findall(rf"\b(\d+)\s+{word}\b", text, flags=re.I))
        counts[word] += len(re.findall(rf":\s+{word}\b", text, flags=re.I))
    return counts


def _target_status(run_returncode: int, results_returncode: int, counts: dict[str, int], *, log_text: str = "") -> str:
    if run_returncode == TIMEOUT_RETURNCODE:
        return "timeout"
    if _no_mutant_test_mapping(run_returncode, results_returncode, counts, log_text):
        return "ok"
    if _command_failed(run_returncode, results_returncode):
        return "error"
    return _status_from_counts(counts)


def _no_mutant_test_mapping(
    run_returncode: int,
    results_returncode: int,
    counts: dict[str, int],
    log_text: str,
) -> bool:
    no_results = results_returncode == 0 and not any(counts.values())
    return (
        run_returncode == 1
        and no_results
        and "could not find any test case for any mutant" in log_text
    )


def _command_failed(run_returncode: int, results_returncode: int) -> bool:
    return run_returncode != 0 or results_returncode != 0


def _status_from_counts(counts: dict[str, int]) -> str:
    return "needs_attention" if any(counts[word] for word in ATTENTION_WORDS) else "ok"


def _exit_code(results: list[TargetResult], *, no_fail: bool) -> int:
    if no_fail:
        return 0
    return 1 if any(result.status != "ok" for result in results) else 0


def _print_summary(results: list[TargetResult], summary_path: Path) -> None:
    print(f"Mutation audit summary: {summary_path}")
    for result in results:
        counts = " ".join(f"{key}={value}" for key, value in sorted(result.counts.items()))
        print(f"- {result.status:15} {result.duration_s:8.1f}s {result.path} {counts}")


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", path).strip("_")


def _shellish(command: list[str]) -> str:
    return " ".join(_quote(part) for part in command)


def _quote(part: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", part):
        return part
    return "'" + part.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
