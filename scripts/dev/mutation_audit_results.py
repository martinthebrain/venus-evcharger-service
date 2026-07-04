# SPDX-License-Identifier: GPL-3.0-or-later
"""Result shaping and summary output for mutation audit runs."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__:
    from . import mutation_audit_support as audit_support
else:
    import mutation_audit_support as audit_support


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


def not_applicable_result(
    *,
    target: audit_support.MutationTarget,
    started: float,
    log_path: Path,
    results_path: Path,
    reason: str,
) -> TargetResult:
    counts = zero_counts()
    counts["skipped"] = 1
    audit_support.write_not_applicable_result(log_path, results_path, target.path, reason)
    return TargetResult(
        path=target.path,
        status="not_applicable",
        duration_s=duration_since(started),
        run_returncode=0,
        results_returncode=0,
        log_path=str(log_path),
        results_path=str(results_path),
        counts=counts,
    )


def no_generated_mutants_result(
    *,
    target: audit_support.MutationTarget,
    started: float,
    artifacts: audit_support.TargetArtifacts,
    run: audit_support.TargetMutmutRun,
) -> TargetResult:
    counts = zero_counts()
    counts["skipped"] = 1
    reason = "mutmut generated no mutant keys for this target"
    append_skip_reason(artifacts.log_path, target.path, reason)
    append_skip_reason(artifacts.results_path, target.path, reason)
    return TargetResult(
        path=target.path,
        status="not_applicable",
        duration_s=duration_since(started),
        run_returncode=run.run_result.returncode,
        results_returncode=run.results_result.returncode,
        log_path=str(artifacts.log_path),
        results_path=str(artifacts.results_path),
        counts=counts,
    )


def runtime_result(
    *,
    target: audit_support.MutationTarget,
    started: float,
    artifacts: audit_support.TargetArtifacts,
    run: audit_support.TargetMutmutRun,
    counts: dict[str, int],
) -> TargetResult:
    return TargetResult(
        path=target.path,
        status=audit_support.target_status(
            run.run_result.returncode,
            run.results_result.returncode,
            counts,
            log_text=run.log_text,
        ),
        duration_s=duration_since(started),
        run_returncode=run.run_result.returncode,
        results_returncode=run.results_result.returncode,
        log_path=str(artifacts.log_path),
        results_path=str(artifacts.results_path),
        counts=counts,
    )


def append_skip_reason(path: Path, target_path: str, reason: str) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"\nSkipping mutation attention for {target_path}: {reason}\n")


def zero_counts() -> dict[str, int]:
    return dict.fromkeys(audit_support.RESULT_WORDS, 0)


def duration_since(started: float) -> float:
    return round(time.monotonic() - started, 3)


def write_summary(results: list[TargetResult], summary_path: Path) -> None:
    summary_path.write_text(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True) + "\n")


def print_summary(results: list[TargetResult], summary_path: Path) -> None:
    print(f"Mutation audit summary: {summary_path}")
    for result in results:
        counts = " ".join(f"{key}={value}" for key, value in sorted(result.counts.items()))
        print(f"- {result.status:15} {result.duration_s:8.1f}s {result.path} {counts}")


def move_verified_mutants_to_killed(counts: dict[str, int], source_status: str, verified: int) -> None:
    counts[source_status] = max(0, counts[source_status] - verified)
    counts["killed"] += verified


def exit_code(results: list[TargetResult], *, no_fail: bool) -> int:
    return audit_support.exit_code([result.status for result in results], no_fail=no_fail)
