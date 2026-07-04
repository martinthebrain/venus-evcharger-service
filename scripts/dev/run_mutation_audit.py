#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a focused mutmut audit for gateway and runtime-safety logic.

The audit is intentionally optional and slow.  It mutates one target module at a
time, writes a full log per target, and creates a compact JSON summary that can
be inspected after a long run.
"""

from __future__ import annotations

import sys

if __package__:
    from . import mutation_audit_cli as audit_cli
    from . import mutation_audit_execution as audit_execution
    from . import mutation_audit_results as audit_results
    from . import mutation_audit_support as audit_support
else:
    import mutation_audit_cli as audit_cli
    import mutation_audit_execution as audit_execution
    import mutation_audit_results as audit_results
    import mutation_audit_support as audit_support


def main(argv: list[str] | None = None) -> int:
    return _run_cli(audit_cli.parse_args(argv, description=__doc__))


def _run_cli(args: object) -> int:
    selected = audit_cli.selected_targets(args.targets)
    if args.list_targets:
        return audit_cli.print_targets(selected)

    repo = audit_cli.repo_dir()
    mutmut = audit_cli.mutmut_command(repo)
    if mutmut is None:
        print("mutmut is required. Install it with: python3 -m pip install mutmut", file=sys.stderr)
        return 2

    out_dir = audit_cli.output_dir(repo, args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    options = audit_execution.TargetRunOptions(
        timeout_s=args.timeout_s,
        reuse_cache=args.reuse_cache,
        verify_survivors=args.verify_survivors,
    )
    with audit_support.mutmut_audit_lock(repo):
        results = [
            audit_execution.run_target(repo=repo, out_dir=out_dir, mutmut=mutmut, target=target, options=options)
            for target in selected
        ]
    summary_path = out_dir / "summary.json"
    audit_results.write_summary(results, summary_path)
    audit_results.print_summary(results, summary_path)
    return audit_results.exit_code(results, no_fail=args.no_fail)


if __name__ == "__main__":
    raise SystemExit(main())
