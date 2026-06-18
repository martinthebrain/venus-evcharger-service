PYTHON ?= python3

.PHONY: test lint pylint-audit security-audit spellcheck shell-audit quality-audit audit typecheck check stress soakcheck mutation-audit

test:
	$(PYTHON) -m unittest

lint:
	./scripts/dev/run_lint.sh

pylint-audit:
	./scripts/dev/run_pylint_audit.sh

security-audit:
	./scripts/dev/run_security_audit.sh

spellcheck:
	./scripts/dev/run_spellcheck.sh

shell-audit:
	./scripts/dev/run_shell_audit.sh

quality-audit:
	./scripts/dev/run_quality_audit.sh

audit:
	./scripts/dev/run_optional_audits.sh

typecheck:
	./scripts/dev/run_typecheck.sh

check:
	./scripts/dev/check_all.sh

stress:
	bash ./scripts/dev/run_stress_tests.sh

soakcheck:
	bash ./scripts/ops/cerbo_soak_check.sh

mutation-audit:
	$(PYTHON) scripts/dev/run_mutation_audit.py
