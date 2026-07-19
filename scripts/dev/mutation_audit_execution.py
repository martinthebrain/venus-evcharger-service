# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-target orchestration for mutation audit runs."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from . import mutation_audit_config as audit_config
    from . import mutation_audit_process as audit_process
    from . import mutation_audit_results as audit_results
    from . import mutation_audit_support as audit_support
    from . import mutation_audit_verification as audit_verification
else:
    import mutation_audit_config as audit_config
    import mutation_audit_process as audit_process
    import mutation_audit_results as audit_results
    import mutation_audit_support as audit_support
    import mutation_audit_verification as audit_verification

MUTMUT_CACHE, MUTMUT_WORKTREE = ".mutmut-cache", "mutants"


@dataclass(frozen=True)
class TargetRunOptions:
    timeout_s: float
    reuse_cache: bool
    verify_survivors: bool


def run_target(
    *,
    repo: Path,
    out_dir: Path,
    mutmut: list[str],
    target: audit_support.MutationTarget,
    options: TargetRunOptions,
) -> audit_results.TargetResult:
    started = time.monotonic()
    artifacts = artifacts_for_target(out_dir, target)
    command = audit_support.MutationCommandContext(repo=repo, mutmut=mutmut)
    not_applicable_reason = audit_support.not_applicable_mutation_target(repo, target.path)
    if not_applicable_reason is not None:
        return audit_results.not_applicable_result(
            target=target,
            started=started,
            log_path=artifacts.log_path,
            results_path=artifacts.results_path,
            reason=not_applicable_reason,
        )
    clear_mutmut_worktree(repo, reuse_cache=options.reuse_cache)
    target_file = repo / target.path
    original_source = target_file.read_bytes()
    try:
        run = run_mutmut_for_target(
            command=command,
            target=target,
            artifacts=artifacts,
            timeout_s=options.timeout_s,
        )
        target_file.write_bytes(original_source)
        if audit_support.generated_no_mutants(repo, target.path):
            return audit_results.no_generated_mutants_result(target=target, started=started, artifacts=artifacts, run=run)
        counts = target_counts(
            command=command,
            target=target,
            run=run,
            artifacts=artifacts,
            verify_survivors=options.verify_survivors,
        )
        return audit_results.runtime_result(target=target, started=started, artifacts=artifacts, run=run, counts=counts)
    finally:
        target_file.write_bytes(original_source)
        clear_target_bytecode(repo, target.path)


def artifacts_for_target(out_dir: Path, target: audit_support.MutationTarget) -> audit_support.TargetArtifacts:
    slug = audit_support.slug(target.path)
    return audit_support.TargetArtifacts(
        log_path=out_dir / f"{slug}.log",
        results_path=out_dir / f"{slug}.results.txt",
    )


def clear_mutmut_worktree(repo: Path, *, reuse_cache: bool) -> None:
    if reuse_cache:
        return
    _remove_generated_path(repo / MUTMUT_CACHE)
    _remove_generated_path(repo / MUTMUT_WORKTREE)


def _remove_generated_path(path: Path) -> None:
    """Remove a local artifact path without following an external symlink."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path, ignore_errors=True)


def clear_target_bytecode(repo: Path, target_path: str) -> None:
    """Remove only cached bytecode that mutmut may leave for the target module."""
    source = repo / target_path
    if source.suffix != ".py":
        return
    cache_dir = source.parent / "__pycache__"
    for cache_path in cache_dir.glob(f"{source.stem}.*.pyc"):
        try:
            cache_path.unlink()
        except OSError:
            pass


def run_mutmut_for_target(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    artifacts: audit_support.TargetArtifacts,
    timeout_s: float,
) -> audit_support.TargetMutmutRun:
    with audit_config.mutmut_config_for_target(command.repo, target.path):
        run_result = audit_process.run_logged(
            [*command.mutmut, "run"],
            cwd=command.repo,
            log_path=artifacts.log_path,
            timeout_s=timeout_s,
        )
        results_result = audit_process.capture_results(
            mutmut=command.mutmut,
            cwd=command.repo,
            results_path=artifacts.results_path,
        )
    return audit_support.TargetMutmutRun(
        run_result=run_result,
        results_result=results_result,
        result_text=read_artifact(artifacts.results_path),
        log_text=read_artifact(artifacts.log_path),
    )


def read_artifact(path: Path) -> str:
    return path.read_text(errors="replace") if path.exists() else ""


def target_counts(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    verify_survivors: bool,
) -> dict[str, int]:
    counts = audit_support.parse_counts(run.result_text)
    if run_has_no_test_mapping(run, counts):
        counts["no_tests"] += 1
    if verify_survivors:
        verify_target_attention_counts(command=command, target=target, run=run, artifacts=artifacts, counts=counts)
    return counts


def run_has_no_test_mapping(run: audit_support.TargetMutmutRun, counts: dict[str, int]) -> bool:
    return audit_support.no_mutant_test_mapping(
        run.run_result.returncode,
        run.results_result.returncode,
        counts,
        run.log_text,
    )


def verify_target_attention_counts(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    counts: dict[str, int],
) -> None:
    if counts["survived"]:
        verify_survived_mutants(command=command, target=target, run=run, artifacts=artifacts, counts=counts)
    if counts["no_tests"]:
        verify_no_test_mutants(command=command, target=target, run=run, artifacts=artifacts, counts=counts)


def verify_survived_mutants(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    counts: dict[str, int],
) -> None:
    verified = audit_verification.verify_survivors(
        repo=command.repo,
        mutmut=command.mutmut,
        target_path=target.path,
        survivor_names=audit_support.mutant_names(run.result_text, "survived"),
        log_path=artifacts.log_path,
        test_selection=audit_config.test_selection_for_target(target.path),
        configure_target=audit_config.mutmut_config_for_target,
    )
    audit_results.move_verified_mutants_to_killed(counts, "survived", verified)


def verify_no_test_mutants(
    *,
    command: audit_support.MutationCommandContext,
    target: audit_support.MutationTarget,
    run: audit_support.TargetMutmutRun,
    artifacts: audit_support.TargetArtifacts,
    counts: dict[str, int],
) -> None:
    verified = audit_verification.verify_mutants(
        repo=command.repo,
        mutmut=command.mutmut,
        target_path=target.path,
        mutant_names=audit_support.mutant_names(run.result_text, "no tests"),
        log_path=artifacts.log_path,
        test_selection=audit_config.test_selection_for_target(target.path),
        configure_target=audit_config.mutmut_config_for_target,
        status_label="no-tests",
    )
    audit_results.move_verified_mutants_to_killed(counts, "no_tests", verified)
