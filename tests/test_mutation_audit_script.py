# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import tomllib

from scripts.dev import (
    mutation_audit_cli,
    mutation_audit_config,
    mutation_audit_execution,
    mutation_audit_process,
    mutation_audit_results,
    mutation_audit_support,
    mutation_audit_targets,
    mutation_audit_verification,
)
from scripts.dev import run_mutation_audit as mutation_audit


def _source_repository_root() -> Path:
    test_repository = Path(__file__).resolve().parents[1]
    if test_repository.name == mutation_audit_execution.MUTMUT_WORKTREE:
        return test_repository.parent
    return test_repository


def _runs_from_mutmut_worktree() -> bool:
    return Path(__file__).resolve().parents[1].name == mutation_audit_execution.MUTMUT_WORKTREE


class MutationAuditScriptTests(unittest.TestCase):
    def test_parse_counts_treats_no_tests_as_attention_result(self) -> None:
        counts = mutation_audit_support.parse_counts(
            """
            2 killed
            1 survived
            3 no tests
            mutant-id: suspicious
            another-id: no tests
            """
        )

        self.assertEqual(counts["killed"], 2)
        self.assertEqual(counts["survived"], 1)
        self.assertEqual(counts["suspicious"], 1)
        self.assertEqual(counts["no_tests"], 4)
        self.assertEqual(counts["timeout"], 0)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(mutation_audit_support.status_from_counts(counts), "needs_attention")

    def test_parse_counts_covers_all_result_words_case_insensitively(self) -> None:
        counts = mutation_audit_support.parse_counts(
            """
            10 KILLED
            2 TIMEOUT
            1 skipped
            mutant-a: suspicious
            mutant-b: killed
            not-a-count: killedish
            """
        )

        self.assertEqual(
            counts,
            {
                "killed": 11,
                "survived": 0,
                "timeout": 2,
                "suspicious": 1,
                "skipped": 1,
                "no_tests": 0,
            },
        )

    def test_survivor_names_extracts_only_survived_mutants(self) -> None:
        self.assertEqual(
            mutation_audit_support.mutant_names(
                """
                package.module.x_func__mutmut_1: survived
                package.module.x_func__mutmut_2: killed
                package.module.x_func__mutmut_3: no tests
                package.module.x_func__mutmut_4: SURVIVED
                package.module.xǁPortǁmethod__mutmut_5: survived
                $ python -m mutmut results --all true: survived
                """,
                "survived",
            ),
            [
                "package.module.x_func__mutmut_1",
                "package.module.x_func__mutmut_4",
                "package.module.xǁPortǁmethod__mutmut_5",
            ],
        )

    def test_target_status_promotes_missing_test_mapping_to_attention(self) -> None:
        counts = dict.fromkeys(mutation_audit_support.RESULT_WORDS, 0)

        status = mutation_audit_support.target_status(
            1,
            0,
            counts,
            log_text="mutmut could not find any test case for any mutant",
        )

        self.assertEqual(status, "needs_attention")

    def test_target_status_keeps_counted_no_tests_as_attention_not_error(self) -> None:
        counts = dict.fromkeys(mutation_audit_support.RESULT_WORDS, 0)
        counts["no_tests"] = 1

        status = mutation_audit_support.target_status(1, 0, counts, log_text="")

        self.assertEqual(status, "needs_attention")

    def test_target_status_vocabulary_and_precedence(self) -> None:
        clean_counts = dict.fromkeys(mutation_audit_support.RESULT_WORDS, 0)
        survived_counts = dict(clean_counts)
        survived_counts["survived"] = 1

        self.assertEqual(
            mutation_audit_support.target_status(mutation_audit_support.TIMEOUT_RETURNCODE, 1, survived_counts, log_text=""),
            "timeout",
        )
        self.assertEqual(mutation_audit_support.target_status(0, 0, survived_counts, log_text=""), "needs_attention")
        self.assertEqual(mutation_audit_support.target_status(0, 1, clean_counts, log_text=""), "error")
        self.assertEqual(mutation_audit_support.target_status(0, 0, clean_counts, log_text=""), "ok")
        self.assertFalse(
            mutation_audit_support.no_mutant_test_mapping(
                1,
                1,
                clean_counts,
                "could not find any test case for any mutant",
            )
        )
        self.assertFalse(
            mutation_audit_support.no_mutant_test_mapping(
                1,
                0,
                survived_counts,
                "could not find any test case for any mutant",
            )
        )

    def test_exit_code_fails_on_no_tests_unless_no_fail_requested(self) -> None:
        self.assertEqual(mutation_audit_support.exit_code(["needs_attention"], no_fail=False), 1)
        self.assertEqual(mutation_audit_support.exit_code(["needs_attention"], no_fail=True), 0)

    def test_exit_code_accepts_explicitly_not_applicable_contract_modules(self) -> None:
        self.assertEqual(mutation_audit_support.exit_code(["not_applicable"], no_fail=False), 0)

    def test_default_mutation_config_contains_update_cycle_contract_tests(self) -> None:
        target = "venus_evcharger/unmapped_runtime.py"
        config = mutation_audit_config.mutmut_config_toml(target)
        parsed = tomllib.loads(config)
        mutmut_config = parsed["tool"]["mutmut"]

        self.assertEqual(mutmut_config["source_paths"], ["venus_evcharger"])
        self.assertEqual(mutmut_config["only_mutate"], [target])
        self.assertEqual(
            mutmut_config["also_copy"],
            [
                "venus_evcharger_auto_input_helper.py",
                "venus_evcharger_dbus_adapter.py",
                "venus_evcharger_service.py",
                "CONTROL_API.md",
                "deploy/venus",
            ],
        )
        self.assertEqual(mutmut_config["pytest_add_cli_args"], ["-k", "not socket"])
        self.assertEqual(
            mutmut_config["pytest_add_cli_args_test_selection"],
            list(mutation_audit_config.DEFAULT_TEST_SELECTION),
        )
        self.assertIn(
            "tests/test_venus_evcharger_control_openapi.py",
            mutmut_config["pytest_add_cli_args_test_selection"],
        )
        self.assertIn(
            "tests/test_venus_evcharger_control_reference.py",
            mutmut_config["pytest_add_cli_args_test_selection"],
        )
        self.assertIn(
            "tests/test_runtime_software_update_setup.py",
            mutmut_config["pytest_add_cli_args_test_selection"],
        )
        self.assertIn(
            "tests/venus_evcharger_ports_write_cases.py",
            mutmut_config["pytest_add_cli_args_test_selection"],
        )
        self.assertIn(
            "venus_evcharger/update/input_cache.py",
            list(mutation_audit_targets.DEFAULT_TARGETS),
        )

    def test_repository_mutmut_selection_references_existing_tests(self) -> None:
        config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        mutmut_config = config["tool"]["mutmut"]
        selection = mutmut_config["pytest_add_cli_args_test_selection"]

        if _runs_from_mutmut_worktree():
            only_mutate = mutmut_config["only_mutate"]
            self.assertEqual(len(only_mutate), 1)
            self.assertEqual(
                selection,
                list(mutation_audit_config.test_selection_for_target(only_mutate[0])),
            )
        else:
            self.assertEqual(
                selection,
                [
                    "tests/test_service_control_runtime_contracts.py",
                    "tests/test_service_control_state_contracts.py",
                ],
            )
        self.assertTrue(all(Path(test_path).is_file() for test_path in selection))

    def test_all_focused_mutation_selections_reference_existing_paths(self) -> None:
        repository = _source_repository_root()
        missing_targets: list[str] = []
        missing_tests: list[str] = []
        for target_prefix, selection in mutation_audit_config.focused_test_selections():
            target = repository / target_prefix
            if not target.exists() and not tuple(target.parent.glob(f"{target.name}*.py")):
                missing_targets.append(target_prefix)
            for test_spec in selection:
                test_path = test_spec.partition("::")[0]
                if not (repository / test_path).is_file():
                    missing_tests.append(test_spec)

        self.assertEqual(missing_targets, [])
        self.assertEqual(missing_tests, [])

    def test_mutation_config_can_target_dev_scripts(self) -> None:
        config = mutation_audit_config.mutmut_config_toml("scripts/dev/mutation_audit_support.py")
        parsed = tomllib.loads(config)
        mutmut_config = parsed["tool"]["mutmut"]

        self.assertEqual(mutmut_config["source_paths"], ["scripts/dev"])
        self.assertEqual(mutmut_config["only_mutate"], ["scripts/dev/mutation_audit_support.py"])
        self.assertEqual(mutmut_config["pytest_add_cli_args_test_selection"], ["tests/test_mutation_audit_script.py"])

    def test_script_mutation_targets_use_focused_test_selection(self) -> None:
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("scripts/dev/run_mutation_audit.py"),
            ("tests/test_mutation_audit_script.py",),
        )

    def test_auto_input_helper_entrypoint_uses_its_runtime_contract_tests(self) -> None:
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger_auto_input_helper.py"),
            ("tests/test_venus_evcharger_auto_input_helper.py",),
        )
        parsed = tomllib.loads(
            mutation_audit_config.mutmut_config_toml("venus_evcharger_auto_input_helper.py")
        )
        self.assertIn("venus_evcharger", parsed["tool"]["mutmut"]["also_copy"])

    def test_grid_fusion_targets_use_focused_contract_selection(self) -> None:
        expected = ("tests/test_grid_measurement_fusion.py",)
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/energy/grid_fusion.py"),
            expected,
        )
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/energy/grid_fusion_contracts.py"),
            expected,
        )
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/inputs/helper/grid_fusion_snapshot.py"),
            expected,
        )

    def test_split_runtime_targets_use_their_contract_suites(self) -> None:
        expected = {
            "venus_evcharger/backend/modbus_transport_serial.py": (
                "tests/test_venus_evcharger_backend_modbus_transport.py",
            ),
            "venus_evcharger/backend/shelly_io_worker_status.py": (
                "tests/test_venus_evcharger_backend_shelly_support.py",
            ),
            "venus_evcharger/backend/shelly_io_ports.py": (
                "tests/test_venus_evcharger_shelly_io_controller.py",
            ),
            "venus_evcharger/bootstrap/wizard_energy_bundle.py": (
                "tests/test_bootstrap_wizard_energy_contracts.py",
            ),
            "venus_evcharger/control/http_api.py": (
                "tests/test_control_http_lifecycle_contracts.py",
            ),
            "venus_evcharger/control/http_api_events.py": (
                "tests/test_control_http_events_contracts.py",
            ),
            "venus_evcharger/control/service.py": (
                "tests/test_control_service_contracts.py",
            ),
            "venus_evcharger/controllers/auto.py": (
                "tests/test_auto_controller_facade_contracts.py",
            ),
            "venus_evcharger/controllers/state.py": (
                "tests/test_state_controller_config_contracts.py",
            ),
            "venus_evcharger/controllers/state_config.py": (
                "tests/test_state_controller_config_contracts.py",
            ),
            "venus_evcharger/controllers/state_json.py": (
                "tests/test_state_json_contracts.py",
            ),
            "venus_evcharger/controllers/state_persistence.py": (
                "tests/test_state_persistence_contracts.py",
            ),
            "venus_evcharger/controllers/state_restore.py": (
                "tests/test_venus_evcharger_state_controller.py",
                "tests/test_state_restore_contracts.py",
            ),
            "venus_evcharger/controllers/state_restore_victron_ess.py": (
                "tests/test_state_restore_victron_ess_contracts.py",
            ),
            "venus_evcharger/controllers/state_runtime_normalize.py": (
                "tests/test_state_runtime_normalize_contracts.py",
            ),
            "venus_evcharger/controllers/state_runtime_overrides.py": (
                "tests/test_state_runtime_overrides_contracts.py",
            ),
            "venus_evcharger/controllers/state_runtime_snapshot.py": (
                "tests/test_state_runtime_snapshot_contracts.py",
                "tests/test_state_runtime_snapshot_defaults_contracts.py",
                "tests/venus_evcharger_state_controller_cases_primary.py",
                "tests/venus_evcharger_state_controller_cases_quaternary.py",
            ),
            "venus_evcharger/controllers/state_runtime_snapshot_victron.py": (
                "tests/test_state_runtime_snapshot_contracts.py",
                "tests/test_state_runtime_snapshot_defaults_contracts.py",
                "tests/test_branch_coverage_next_cluster_two.py",
            ),
            "venus_evcharger/controllers/state_specs.py": (
                "tests/test_state_runtime_overrides_contracts.py",
            ),
            "venus_evcharger/controllers/state_summary.py": (
                "tests/test_state_summary_contracts.py",
                "tests/test_state_summary_edge_contracts.py",
                "tests/venus_evcharger_state_controller_cases_primary.py",
                "tests/venus_evcharger_state_controller_cases_quaternary.py",
            ),
            "venus_evcharger/controllers/state_validation.py": (
                "tests/test_state_validation_contracts.py",
                "tests/test_state_validation_logging_contracts.py",
                "tests/venus_evcharger_state_controller_cases_tertiary.py",
            ),
            "venus_evcharger/controllers/write.py": (
                "tests/test_write_controller_contracts.py",
                "tests/test_write_controller_handler_contracts.py",
                "tests/test_venus_evcharger_write_controller.py",
            ),
            "venus_evcharger/controllers/write_snapshot.py": (
                "tests/test_write_snapshot_contracts.py",
                "tests/test_venus_evcharger_write_controller.py",
                "tests/test_remaining_coverage_helpers.py",
            ),
            "venus_evcharger/controllers/write_support.py": (
                "tests/test_write_support_contracts.py",
                "tests/test_write_controller_handler_contracts.py",
                "tests/test_venus_evcharger_write_controller.py",
            ),
            "venus_evcharger/core/common.py": (
                "tests/test_core_common_boundary_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/common_auto.py": (
                "tests/test_core_common_auto_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/common_schedule.py": (
                "tests/test_core_common_schedule_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/common_types.py": (
                "tests/test_core_common_types_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/common_values.py": (
                "tests/test_core_common_values_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/contracts_basic.py": (
                "tests/test_core_contracts_basic_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/contracts_bootstrap.py": (
                "tests/test_core_contracts_bootstrap_contracts.py",
                "tests/test_bootstrap_contracts.py",
            ),
            "venus_evcharger/core/contracts_control.py": (
                "tests/test_core_contracts_control_command_contracts.py",
                "tests/test_core_contracts_control_api_contracts.py",
                "tests/test_venus_evcharger_control_contracts.py",
            ),
            "venus_evcharger/core/contracts_outward.py": (
                "tests/test_core_contracts_outward_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/contracts_snapshot.py": (
                "tests/test_core_contracts_snapshot_contracts.py",
                "tests/test_venus_evcharger_common.py",
            ),
            "venus_evcharger/core/contracts_state_endpoints.py": (
                "tests/test_core_contracts_state_endpoints_contracts.py",
                "tests/test_venus_evcharger_state_contracts.py",
            ),
            "venus_evcharger/core/contracts_state_operational.py": (
                "tests/test_venus_evcharger_state_contracts.py",
            ),
            "venus_evcharger/core/contracts_state_shared.py": (
                "tests/test_core_contracts_state_shared_contracts.py",
                "tests/test_core_contracts_state_endpoints_contracts.py",
                "tests/test_venus_evcharger_state_contracts.py",
            ),
            "venus_evcharger/core/return_contracts.py": (
                "tests/test_core_return_contracts.py",
                "tests/test_runtime_async_mainloop_types.py",
            ),
            "venus_evcharger/core/shared.py": (
                "tests/test_core_shared_contracts.py",
                "tests/test_venus_evcharger_shared.py",
            ),
            "venus_evcharger/dbus_adapter/rate.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py",
            ),
            "venus_evcharger/dbus_adapter/resources.py": (
                "tests/test_dbus_adapter_resource_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/scheduling.py": (
                "tests/test_dbus_adapter_scheduler_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/health/backpressure.py": (
                "tests/test_dbus_adapter_backpressure_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/health/freshness.py": (
                "tests/test_dbus_adapter_freshness_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/health/history.py": (
                "tests/test_dbus_adapter_health_history_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/health/queue.py": (
                "tests/test_dbus_adapter_health_queue_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/health/slo.py": (
                "tests/test_dbus_adapter_health_slo_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/jsonl.py": (
                "tests/test_dbus_adapter_jsonl_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/process/adapter.py": ("tests/test_dbus_adapter_process_contracts.py",),
            "venus_evcharger/dbus_adapter/process/config.py": (
                "tests/test_dbus_adapter_process_config_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/process/identity.py": (
                "tests/test_dbus_adapter_process_identity_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/process/introspection.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_processes_legacy_introspection_request_file",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_introspection_request_and_background_edges",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_introspection_request_contracts",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_introspection_file_payload_uses_utf8_and_dict_payloads",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_enqueue_introspection_requests_contracts",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_introspection_enqueue_command_payload_contracts",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_background_introspection_spec_contracts",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_gateway_non_write_introspection_command_contracts",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_non_write_introspection_timed_logging_main_and_json_ready",
            ),
            "venus_evcharger/dbus_adapter/process/introspection_snapshot.py": (
                "tests/test_dbus_adapter_introspection_snapshot_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/process/io.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_poll_and_discovery_contracts",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_poll_and_discovery_edges",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_cache_publish_interval_contracts",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_signal_handlers_andlist_services_edges",
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_timed_operation_contracts_record_latency_and_errors",
            ),
            "venus_evcharger/dbus_adapter/process/runtime.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_signal_handlers_andlist_services_edges",
            ),
            "venus_evcharger/dbus_adapter/process/socket.py": (
                "tests/test_dbus_adapter_process_ipc_contracts.py",
            ),
            "venus_evcharger/dbus_adapter/read/aggregate.py": ("tests/test_dbus_adapter_read_aggregate_contracts.py",),
            "venus_evcharger/dbus_adapter/read/targets.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_read_target_contract_requires_service_and_absolute_path",
            ),
            "venus_evcharger/dbus_adapter/read/spec.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_read_spec_from_mapping_validates_known_fields",
            ),
            "venus_evcharger/dbus_adapter/write/scheduler.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_write_scheduler_health_budgets_lifecycle_and_remote_write_edges",
            ),
            "venus_evcharger/dbus_adapter/write/support.py": (
                "tests/test_dbus_gateway_adapter_scheduler.py::DbusGatewayAdapterSchedulerTests::test_write_scheduler_support_helper_contracts",
            ),
            "venus_evcharger/dbus_gateway_surface.py": ("tests/test_dbus_gateway_primitives.py",),
            "venus_evcharger/dbus_introspection.py": (
                "tests/test_dbus_introspection_gateway_cache.py",
                "tests/test_dbus_introspection_contracts.py",
            ),
            "venus_evcharger/energy/config.py": (
                "tests/test_energy_config_contracts.py",
            ),
            "venus_evcharger/energy/connectors.py": (
                "tests/test_energy_connectors_facade_contracts.py",
            ),
            "venus_evcharger/energy/connectors_command.py": (
                "tests/test_energy_connectors_command_contracts.py",
            ),
            "venus_evcharger/energy/connectors_common.py": (
                "tests/test_energy_connectors_common_contracts.py",
            ),
            "venus_evcharger/energy/connectors_modbus.py": (
                "tests/test_energy_connectors_modbus_contracts.py",
            ),
            "venus_evcharger/energy/connectors_opendtu.py": (
                "tests/test_energy_connectors_opendtu_contracts.py",
            ),
            "venus_evcharger/energy/connectors_template.py": (
                "tests/test_energy_connectors_template_contracts.py",
            ),
            "venus_evcharger/energy/learning.py": (
                "tests/test_venus_evcharger_energy_aggregate.py",
            ),
            "venus_evcharger/energy/learning_coercion.py": (
                "tests/test_energy_learning_coercion_contracts.py",
            ),
            "venus_evcharger/energy/probe.py": (
                "tests/test_energy_probe_facade_contracts.py",
                "tests/test_venus_evcharger_energy_probe.py",
            ),
            "venus_evcharger/energy/probe_cli.py": (
                "tests/test_energy_probe_cli_contracts.py",
            ),
            "venus_evcharger/energy/probe_core.py": (
                "tests/test_energy_probe_core_contracts.py",
            ),
            "venus_evcharger/energy/probe_huawei.py": (
                "tests/test_energy_probe_huawei_contracts.py",
            ),
            "venus_evcharger/energy/profiles.py": (
                "tests/test_energy_profiles_contracts.py",
            ),
            "venus_evcharger/energy/recommendation_schema.py": (
                "tests/test_energy_recommendation_schema_contracts.py",
            ),
            "venus_evcharger/inputs/dbus.py": (
                "tests/test_input_boundary_contracts.py",
                "tests/test_venus_evcharger_dbus_inputs_controller.py",
                "tests/test_input_edge_contracts.py",
                "tests/test_storage_gateway_boundary_contracts.py",
            ),
            "venus_evcharger/inputs/energy_snapshot_contracts.py": (
                "tests/test_input_boundary_contracts.py",
            ),
            "venus_evcharger/inputs/gateway_read.py": (
                "tests/test_gateway_input_reader_contracts.py",
                "tests/test_input_edge_contracts.py",
                "tests/test_storage_gateway_boundary_contracts.py",
            ),
            "venus_evcharger/inputs/helper/capacity_persistence.py": (
                "tests/test_auto_input_capacity_persistence.py",
            ),
            "venus_evcharger/inputs/helper/config_runtime.py": (
                "tests/test_auto_input_helper_config_contracts.py",
                "tests/test_venus_evcharger_auto_input_helper.py",
            ),
            "venus_evcharger/inputs/helper/snapshot.py": (
                "tests/test_auto_input_helper_snapshot_liveness_contracts.py",
            ),
            "venus_evcharger/inputs/helper/sources.py": (
                "tests/test_auto_input_helper_sources_contracts.py",
                "tests/test_auto_input_helper_snapshot_liveness_contracts.py",
            ),
            "venus_evcharger/inputs/helper/sources_dbus_primary.py": (
                "tests/test_auto_input_helper_sources_contracts.py",
            ),
            "venus_evcharger/inputs/helper/sources_dbus_resolve.py": (
                "tests/test_auto_input_helper_sources_contracts.py",
            ),
            "venus_evcharger/runtime/async_mainloop_control.py": (
                "tests/test_runtime_async_mainloop_control_contracts.py",
                "tests/test_venus_evcharger_runtime_support_controller.py",
            ),
            "venus_evcharger/runtime/async_mainloop_publish.py": (
                "tests/test_runtime_async_mainloop_publish_contracts.py",
                "tests/test_venus_evcharger_runtime_support_controller.py",
            ),
            "venus_evcharger/runtime/async_mainloop_state.py": (
                "tests/test_runtime_async_mainloop_state_contracts.py",
                "tests/test_venus_evcharger_runtime_support_controller.py",
            ),
            "venus_evcharger/runtime/async_mainloop_types.py": (
                "tests/test_runtime_async_mainloop_types.py",
            ),
            "venus_evcharger/runtime/async_mainloop_watchdog.py": (
                "tests/test_runtime_async_mainloop_watchdog_contracts.py",
            ),
            "venus_evcharger/runtime/audit.py": (
                "tests/test_runtime_audit_contracts.py",
                "tests/test_venus_evcharger_runtime_support_controller.py",
            ),
            "venus_evcharger/runtime/audit_fields.py": (
                "tests/test_runtime_audit_fields_contracts.py",
            ),
            "venus_evcharger/runtime/health.py": (
                "tests/test_runtime_health_contracts.py",
            ),
            "venus_evcharger/runtime/setup.py": (
                "tests/test_runtime_setup_contracts.py",
            ),
            "venus_evcharger/runtime/setup_support.py": (
                "tests/test_runtime_setup_support_contracts.py",
            ),
            "venus_evcharger/runtime/software_update_setup.py": (
                "tests/test_runtime_software_update_setup.py",
            ),
            "venus_evcharger/runtime/support.py": (
                "tests/test_runtime_support_contracts.py",
            ),
            "venus_evcharger/service/control_runtime.py": (
                "tests/test_service_control_runtime_contracts.py",
                "tests/test_service_control_state_contracts.py",
            ),
        }
        for target, selection in expected.items():
            with self.subTest(target=target):
                self.assertEqual(mutation_audit_config.test_selection_for_target(target), selection)

    def test_bootstrap_mutation_targets_use_focused_test_selection(self) -> None:
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/bootstrap/config_identity.py"),
            (
                "tests/test_venus_evcharger_bootstrap_controller.py",
                "tests/test_auto_backend_bootstrap_edge_contracts.py",
            ),
        )

    def test_update_runtime_mutation_targets_include_full_scenarios(self) -> None:
        module_names = (
            "offline_publish.py",
            "pm_snapshot.py",
            "readback_resolver.py",
            "relay_charger_current.py",
            "relay_charger_current_targets.py",
            "relay_charger_health.py",
            "relay_charger_transport.py",
            "relay_phase_decision.py",
            "relay_phase_publish.py",
            "relay_phase_switch_policy.py",
            "relay_status_publish.py",
            "state.py",
        )
        for module_name in module_names:
            with self.subTest(module_name=module_name):
                selection = mutation_audit_config.test_selection_for_target(
                    f"venus_evcharger/update/{module_name}"
                )
                self.assertIn("tests/test_venus_evcharger_update_cycle_controller.py", selection)
                self.assertIn("tests/test_update_edge_contracts.py", selection)

    def test_new_runtime_contract_suites_are_bound_to_their_mutation_targets(self) -> None:
        contract_targets = {
            "tests/test_update_time_safety_contracts.py": (
                "venus_evcharger/update/pm_snapshot.py",
                "venus_evcharger/update/relay_charger_current_targets.py",
                "venus_evcharger/update/state.py",
            ),
            "tests/test_update_input_validation_contracts.py": (
                "venus_evcharger/update/offline_publish.py",
                "venus_evcharger/update/relay_phase_decision.py",
            ),
            "tests/test_phase_switch_persistence_contracts.py": (
                "venus_evcharger/update/relay_phase_switch_policy.py",
            ),
            "tests/test_storage_cluster_resilience_contracts.py": (
                "venus_evcharger/inputs/storage.py",
            ),
            "tests/test_storage_gateway_boundary_contracts.py": (
                "venus_evcharger/inputs/dbus.py",
                "venus_evcharger/inputs/gateway_read.py",
                "venus_evcharger/inputs/storage_support.py",
                "venus_evcharger/ports/dbus.py",
                "venus_evcharger/service/composition_guards.py",
            ),
        }
        for contract_suite, targets in contract_targets.items():
            for target in targets:
                with self.subTest(contract_suite=contract_suite, target=target):
                    self.assertIn(
                        contract_suite,
                        mutation_audit_config.test_selection_for_target(target),
                    )

    def test_edge_contract_suites_are_bound_to_their_mutation_targets(self) -> None:
        edge_targets = {
            "tests/test_auto_backend_bootstrap_edge_contracts.py": (
                "venus_evcharger/auto/logic_samples.py",
                "venus_evcharger/bootstrap/config_backend.py",
                "venus_evcharger/bootstrap/config_identity.py",
                "venus_evcharger/bootstrap/controller.py",
            ),
            "tests/test_boundary_port_edge_contracts.py": (
                "venus_evcharger/bootstrap/contracts.py",
                "venus_evcharger/ports/write.py",
                "venus_evcharger/ports/write_runtime.py",
                "venus_evcharger/publish/dbus_diagnostics_sources.py",
            ),
            "tests/test_input_edge_contracts.py": (
                "venus_evcharger/inputs/dbus.py",
                "venus_evcharger/inputs/gateway_read.py",
                "venus_evcharger/inputs/storage.py",
                "venus_evcharger/inputs/storage_support.py",
                "venus_evcharger/inputs/supervisor.py",
                "venus_evcharger/inputs/supervisor_process.py",
                "venus_evcharger/inputs/supervisor_snapshot_runtime.py",
                "venus_evcharger/inputs/supervisor_snapshot_validation.py",
            ),
            "tests/test_service_control_state_contracts.py": (
                "venus_evcharger/service/control.py",
                "venus_evcharger/service/control_runtime.py",
                "venus_evcharger/service/control_state_config.py",
                "venus_evcharger/service/control_state_core.py",
                "venus_evcharger/service/control_state_meta.py",
            ),
            "tests/test_service_operational_state_contracts.py": (
                "venus_evcharger/service/control_state_operational.py",
                "venus_evcharger/service/control_state_operational_support.py",
                "venus_evcharger/service/control_state_victron.py",
            ),
            "tests/test_update_edge_contracts.py": (
                "venus_evcharger/update/offline_publish.py",
                "venus_evcharger/update/pm_snapshot.py",
                "venus_evcharger/update/readback_resolver.py",
                "venus_evcharger/update/relay_charger_current.py",
                "venus_evcharger/update/relay_charger_current_targets.py",
                "venus_evcharger/update/relay_charger_health.py",
                "venus_evcharger/update/relay_charger_transport.py",
                "venus_evcharger/update/relay_phase_decision.py",
                "venus_evcharger/update/relay_phase_publish.py",
                "venus_evcharger/update/relay_phase_switch_policy.py",
                "venus_evcharger/update/relay_status_publish.py",
                "venus_evcharger/update/state.py",
            ),
        }
        for edge_suite, targets in edge_targets.items():
            for target in targets:
                with self.subTest(edge_suite=edge_suite, target=target):
                    self.assertIn(edge_suite, mutation_audit_config.test_selection_for_target(target))

    def test_backend_mutation_targets_use_backend_contract_selection(self) -> None:
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/backend/config.py"),
            (
                "tests/test_backend_config_file.py",
                "tests/test_venus_evcharger_backend_factory.py",
                "tests/test_venus_evcharger_backend_probe.py",
                "tests/test_backend_factory_probe_contracts.py",
                "tests/test_topology_config.py",
            ),
        )

    def test_template_backend_mutation_targets_use_template_contract_selection(self) -> None:
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/backend/template_meter.py"),
            (
                "tests/test_venus_evcharger_backend_template_meter.py",
                "tests/test_venus_evcharger_backend_template_support.py",
            ),
        )
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/backend/template_switch.py"),
            (
                "tests/test_venus_evcharger_backend_template_switch.py",
                "tests/test_venus_evcharger_backend_template_support.py",
                "tests/test_venus_evcharger_backend_tuya.py",
            ),
        )
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/backend/template_charger.py"),
            (
                "tests/test_venus_evcharger_backend_template_charger.py",
                "tests/test_venus_evcharger_backend_template_support.py",
            ),
        )
        self.assertEqual(
            mutation_audit_config.test_selection_for_target("venus_evcharger/backend/template_support.py"),
            (
                "tests/test_venus_evcharger_backend_template_support.py",
                "tests/test_venus_evcharger_backend_template_meter.py",
                "tests/test_venus_evcharger_backend_template_switch.py",
                "tests/test_venus_evcharger_backend_template_charger.py",
            ),
        )

    def test_constant_only_modules_are_reported_as_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            path = repo / "package" / "contracts.py"
            path.parent.mkdir()
            path.write_text(
                '''"""Contract constants."""\n\nfrom typing import Final\n\nVALUE: Final = 1\nNAME = "demo"\n''',
                encoding="utf-8",
            )

            self.assertTrue(mutation_audit_support.is_constant_only_module(path))
            self.assertEqual(
                mutation_audit_support.not_applicable_mutation_target(repo, "package/contracts.py"),
                "constant-only module; mutation coverage belongs to consuming runtime modules",
            )

    def test_empty_mutmut_metadata_means_no_generated_mutants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            meta_path = repo / "mutants" / "package" / "module.py.meta"
            meta_path.parent.mkdir(parents=True)
            meta_path.write_text(
                """{
                    "exit_code_by_key": {},
                    "type_check_error_by_key": {},
                    "durations_by_key": {},
                    "estimated_durations_by_key": {}
                }""",
                encoding="utf-8",
            )

            self.assertTrue(mutation_audit_support.generated_no_mutants(repo, "package/module.py"))

    def test_non_empty_mutmut_metadata_keeps_target_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            meta_path = repo / "mutants" / "package" / "module.py.meta"
            meta_path.parent.mkdir(parents=True)
            meta_path.write_text(
                """{
                    "exit_code_by_key": {"package.module.x_case__mutmut_1": 1},
                    "type_check_error_by_key": {},
                    "durations_by_key": {},
                    "estimated_durations_by_key": {}
                }""",
                encoding="utf-8",
            )

            self.assertFalse(mutation_audit_support.generated_no_mutants(repo, "package/module.py"))

    def test_non_constant_modules_stay_mutation_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            path = repo / "package" / "runtime.py"
            path.parent.mkdir()
            path.write_text("def value() -> int:\n    return 1\n", encoding="utf-8")

            self.assertFalse(mutation_audit_support.is_constant_only_module(path))
            self.assertIsNone(mutation_audit_support.not_applicable_mutation_target(repo, "package/runtime.py"))

    def test_survivor_verification_uses_mutmut_virtualenv_when_available(self) -> None:
        command = mutation_audit_verification.survivor_verification_test_command(
            ["/tmp/python", "-m", "mutmut"],
            mutation_audit_config.DEFAULT_TEST_SELECTION,
        )

        self.assertEqual(command[:4], ["/tmp/python", "-m", "pytest", "-q"])
        self.assertIn("tests/venus_evcharger_update_cycle_controller_cases_tertiary.py", command)
        self.assertIn("tests/test_runtime_software_update_setup.py", command)
        self.assertIn("tests/venus_evcharger_ports_write_cases.py", command)

    def test_cli_argument_contracts_cover_defaults_and_explicit_options(self) -> None:
        defaults = mutation_audit_cli.parse_args([], description="Mutation audit")

        self.assertEqual(defaults.targets, [])
        self.assertFalse(defaults.list_targets)
        self.assertIsNone(defaults.out_dir)
        self.assertEqual(defaults.timeout_s, 1800.0)
        self.assertFalse(defaults.reuse_cache)
        self.assertTrue(defaults.verify_survivors)
        self.assertFalse(defaults.no_fail)

        explicit = mutation_audit_cli.parse_args(
            [
                "--list-targets",
                "--out-dir",
                "/tmp/mutation-out",
                "--timeout-s",
                "12.5",
                "--reuse-cache",
                "--verify-survivors",
                "--no-fail",
                "a.py",
                "b.py",
            ],
            description="Mutation audit",
        )

        self.assertEqual(explicit.targets, ["a.py", "b.py"])
        self.assertTrue(explicit.list_targets)
        self.assertEqual(explicit.out_dir, "/tmp/mutation-out")
        self.assertEqual(explicit.timeout_s, 12.5)
        self.assertTrue(explicit.reuse_cache)
        self.assertTrue(explicit.verify_survivors)
        self.assertTrue(explicit.no_fail)

        fast = mutation_audit_cli.parse_args(["--no-verify-survivors"], description="Mutation audit")
        self.assertFalse(fast.verify_survivors)

        help_output = StringIO()
        with self.assertRaises(SystemExit) as help_exit:
            with redirect_stdout(help_output):
                mutation_audit_cli.parse_args(["--help"], description="Mutation audit")

        self.assertEqual(help_exit.exception.code, 0)
        help_text = help_output.getvalue()
        self.assertIn("Mutation audit", help_text)
        self.assertIn("Target module path(s). Defaults to the curated gateway", help_text)
        self.assertIn("list.", help_text)
        self.assertIn("Print selected targets and exit.", help_text)
        self.assertIn("Output directory for logs. Defaults to", help_text)
        self.assertIn("build/mutation-", help_text)
        self.assertIn("audit/<timestamp>.", help_text)
        self.assertIn("Timeout per mutated module.", help_text)
        self.assertIn("Do not clear .mutmut-cache between target modules.", help_text)
        self.assertIn("Re-apply surviving mutants and run the selected tests", help_text)
        self.assertIn("in a clean subprocess.", help_text)
        self.assertIn("Always exit 0 after writing logs and summary.", help_text)
        self.assertNotIn("XX", help_text)

    def test_cli_target_and_output_directory_contracts(self) -> None:
        explicit = mutation_audit_cli.selected_targets(["one.py", "two.py"])
        defaults = mutation_audit_cli.selected_targets([])

        self.assertEqual([target.path for target in explicit], ["one.py", "two.py"])
        self.assertGreater(len(defaults), 10)
        self.assertEqual(defaults[0].path, "venus_evcharger/dbus_gateway_policy.py")

        repo = Path("/tmp/repo")
        with patch.object(mutation_audit_cli.time, "strftime", return_value="20260704-081500") as strftime:
            self.assertEqual(
                mutation_audit_cli.output_dir(repo, None),
                repo / "build" / "mutation-audit" / "20260704-081500",
            )
        strftime.assert_called_once_with("%Y%m%d-%H%M%S")

        self.assertEqual(mutation_audit_cli.output_dir(repo, "/tmp/out"), Path("/tmp/out"))

    def test_mutation_audit_lock_rejects_concurrent_runs_in_same_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            with mutation_audit_support.mutmut_audit_lock(repo):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with mutation_audit_support.mutmut_audit_lock(repo):
                        pass

            with mutation_audit_support.mutmut_audit_lock(repo):
                self.assertTrue((repo / mutation_audit_support.LOCK_FILENAME).exists())

    def test_shell_formatting_helpers_quote_only_when_needed(self) -> None:
        support = mutation_audit_support

        self.assertEqual(support.slug("venus_evcharger/update/runtime.py"), "venus_evcharger_update_runtime.py")
        self.assertEqual(support.slug("ABC/path.py"), "ABC_path.py")
        self.assertEqual(support.slug("/tmp/a b/c.py"), "tmp_a_b_c.py")
        self.assertEqual(support.slug("already.ok-1_2.py"), "already.ok-1_2.py")
        self.assertEqual(support.slug("X_name_X"), "X_name_X")
        self.assertEqual(support.quote("simple-1_2/ok.py"), "simple-1_2/ok.py")
        self.assertEqual(support.quote("ABC"), "ABC")
        self.assertEqual(support.quote(""), "''")
        self.assertEqual(support.quote("has space"), "'has space'")
        self.assertEqual(support.quote("can't"), "'can'\"'\"'t'")
        self.assertEqual(
            support.shellish(["python3", "-m", "pkg tool", "can't"]),
            "python3 -m 'pkg tool' 'can'\"'\"'t'",
        )

    def test_support_status_helpers_cover_timeout_command_and_clean_outcomes(self) -> None:
        support = mutation_audit_support
        clean_counts = dict.fromkeys(support.RESULT_WORDS, 0)
        attention_counts = dict(clean_counts)
        attention_counts["survived"] = 1

        self.assertTrue(support.command_failed(1, 0))
        self.assertFalse(support.command_failed(0, 0))
        self.assertEqual(support.status_from_counts(clean_counts), "ok")
        self.assertEqual(support.status_from_counts(attention_counts), "needs_attention")
        self.assertEqual(support.target_status(support.TIMEOUT_RETURNCODE, 0, clean_counts, log_text=""), "timeout")
        self.assertEqual(support.target_status(1, 0, clean_counts, log_text="plain failure"), "error")
        self.assertEqual(
            support.target_status(
                1,
                0,
                clean_counts,
                log_text="could not find any test case for any mutant",
            ),
            "needs_attention",
        )

    def test_constant_detection_handles_docstrings_imports_and_type_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            constants = repo / "constants.py"
            constants.write_text(
                '"""Doc."""\nfrom typing import Final, TypeAlias\nVALUE = 1\nNAME: Final = "x"\nAlias: TypeAlias = str\n',
                encoding="utf-8",
            )
            imports_only = repo / "imports_only.py"
            imports_only.write_text("import os\nfrom typing import Final\n", encoding="utf-8")
            expression = repo / "expression.py"
            expression.write_text('"""Doc."""\n42\nVALUE = 1\n', encoding="utf-8")
            runtime = repo / "runtime.py"
            runtime.write_text('"""Doc."""\nVALUE = 1\ndef value() -> int:\n    return VALUE\n', encoding="utf-8")
            invalid = repo / "invalid.py"
            invalid.write_text("def broken(:\n", encoding="utf-8")

            self.assertTrue(mutation_audit_support.is_constant_only_module(constants))
            self.assertFalse(mutation_audit_support.is_constant_only_module(imports_only))
            self.assertFalse(mutation_audit_support.is_constant_only_module(expression))
            self.assertFalse(mutation_audit_support.is_constant_only_module(runtime))
            self.assertFalse(mutation_audit_support.is_constant_only_module(invalid))
            synthetic_type_alias = type("TypeAlias", (), {})()
            self.assertTrue(mutation_audit_support.is_constant_assignment(synthetic_type_alias))

    def test_constant_detection_reads_source_as_utf8(self) -> None:
        source_path = Path("/repo/constants.py")

        def read_text(path: Path, *args: object, **kwargs: object) -> str:
            self.assertEqual(path, source_path)
            self.assertFalse(args)
            self.assertEqual(kwargs, {"encoding": "utf-8"})
            return "VALUE = 1\n"

        with patch.object(Path, "read_text", autospec=True, side_effect=read_text):
            self.assertTrue(mutation_audit_support.is_constant_only_module(source_path))

    def test_generated_no_mutants_requires_all_mutant_maps_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            meta_path = repo / "mutants" / "target.py.meta"
            meta_path.parent.mkdir()

            self.assertFalse(mutation_audit_support.generated_no_mutants(repo, "target.py"))
            meta_path.write_text("{bad json", encoding="utf-8")
            self.assertFalse(mutation_audit_support.generated_no_mutants(repo, "target.py"))
            meta_path.write_text(
                '{"exit_code_by_key": {}, "type_check_error_by_key": {}, "durations_by_key": {}}',
                encoding="utf-8",
            )
            self.assertFalse(mutation_audit_support.generated_no_mutants(repo, "target.py"))
            meta_path.write_text(
                '{"exit_code_by_key": {}, "type_check_error_by_key": {}, "durations_by_key": {}, "estimated_durations_by_key": {"x": 1}}',
                encoding="utf-8",
            )
            self.assertFalse(mutation_audit_support.generated_no_mutants(repo, "target.py"))

    def test_generated_no_mutants_reads_expected_metadata_as_utf8(self) -> None:
        repo = Path("/repo")
        expected_path = repo / "mutants" / "target.py.meta"

        def read_text(path: Path, *args: object, **kwargs: object) -> str:
            self.assertEqual(path, expected_path)
            self.assertFalse(args)
            self.assertEqual(kwargs, {"encoding": "utf-8"})
            return (
                '{"exit_code_by_key": {}, "type_check_error_by_key": {}, '
                '"durations_by_key": {}, "estimated_durations_by_key": {}}'
            )

        with patch.object(Path, "read_text", autospec=True, side_effect=read_text):
            self.assertTrue(mutation_audit_support.generated_no_mutants(repo, "target.py"))

    def test_write_not_applicable_result_writes_log_and_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = root / "target.log"
            results_path = root / "target.results"

            mutation_audit_support.write_not_applicable_result(
                log_path,
                results_path,
                "target.py",
                "reason",
            )

            expected = "Skipping mutation audit for target.py: reason\n"
            self.assertEqual(log_path.read_text(encoding="utf-8"), expected)
            self.assertEqual(results_path.read_text(encoding="utf-8"), expected)

    def test_write_not_applicable_result_writes_exact_utf8_payloads(self) -> None:
        log_path = Path("/tmp/target.log")
        results_path = Path("/tmp/target.results")
        writes: list[tuple[Path, str, dict[str, object]]] = []

        def write_text(path: Path, text: str, *args: object, **kwargs: object) -> int:
            self.assertFalse(args)
            writes.append((path, text, kwargs))
            return len(text)

        with patch.object(Path, "write_text", autospec=True, side_effect=write_text):
            mutation_audit_support.write_not_applicable_result(
                log_path,
                results_path,
                "target.py",
                "reason",
            )

        expected = "Skipping mutation audit for target.py: reason\n"
        self.assertEqual(
            writes,
            [
                (log_path, expected, {"encoding": "utf-8"}),
                (results_path, expected, {"encoding": "utf-8"}),
            ],
        )


    def test_verify_mutants_restores_source_and_counts_failed_verifications(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = repo / "target.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            log_path = repo / "verify.log"

            def fake_check(**kwargs: object) -> bool:
                self.assertEqual(kwargs["repo"], repo)
                self.assertEqual(kwargs["mutmut"], ["python3", "-m", "mutmut"])
                self.assertEqual(kwargs["target_file"], target)
                self.assertEqual(kwargs["original"], b"VALUE = 1\n")
                self.assertIsNotNone(kwargs["log"])
                self.assertEqual(kwargs["test_selection"], ("tests/example.py",))
                return kwargs["mutant"] == "mutant-a"

            with patch.object(mutation_audit_verification, "mutant_fails_selected_tests", side_effect=fake_check):
                verified = mutation_audit_verification.verify_mutants(
                    repo=repo,
                    mutmut=["python3", "-m", "mutmut"],
                    target_path="target.py",
                    mutant_names=["mutant-a", "mutant-b"],
                    log_path=log_path,
                    test_selection=("tests/example.py",),
                    configure_target=lambda _repo, _target: _null_context(),
                    status_label="survived",
                )

            self.assertEqual(verified, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertIn("Verifying survived mutants", log_path.read_text(encoding="utf-8"))

    def test_verify_mutants_uses_utf8_append_log_and_target_context(self) -> None:
        opened: list[tuple[Path, str, str | None]] = []
        configured: list[tuple[Path, str, str]] = []
        log = _MemoryTextWriter()

        class StrictContext:
            def __enter__(self) -> None:
                configured.append((repo, "target.py", "enter"))
                return None

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                configured.append((repo, "target.py", "exit"))

        def configure(target_repo: Path, target_path: str) -> StrictContext:
            self.assertEqual(target_repo, repo)
            self.assertEqual(target_path, "target.py")
            return StrictContext()

        def open_path(path: Path, mode: str = "r", *args: object, **kwargs: object) -> _MemoryTextWriter:
            opened.append((path, mode, kwargs.get("encoding")))
            self.assertEqual(path, log_path)
            self.assertEqual(mode, "a")
            self.assertEqual(kwargs, {"encoding": "utf-8"})
            self.assertEqual(args, ())
            return log

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            log_path = repo / "verify.log"
            with patch.object(Path, "read_bytes", autospec=True, return_value=b"original\n"):
                with patch.object(Path, "write_bytes", autospec=True):
                    with patch.object(Path, "open", autospec=True, side_effect=open_path):
                        with patch.object(
                            mutation_audit_verification,
                            "mutant_fails_selected_tests",
                            side_effect=[True, False, True],
                        ):
                            verified = mutation_audit_verification.verify_mutants(
                                repo=repo,
                                mutmut=["python3", "-m", "mutmut"],
                                target_path="target.py",
                                mutant_names=["mutant-a", "mutant-b", "mutant-c"],
                                log_path=log_path,
                                test_selection=("tests/example.py",),
                                configure_target=configure,
                                status_label="survived",
                            )

        self.assertEqual(verified, 2)
        self.assertEqual(opened, [(log_path, "a", "utf-8")])
        self.assertEqual(configured, [(repo, "target.py", "enter"), (repo, "target.py", "exit")])
        self.assertEqual(log.text, "\nVerifying survived mutants in clean subprocesses\n")

    def test_mutant_fails_selected_tests_restores_source_after_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = repo / "target.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            with (repo / "verify.log").open("w", encoding="utf-8") as log:
                with patch.object(mutation_audit_verification, "apply_mutant", return_value=True) as apply:
                    with patch.object(mutation_audit_verification, "run_survivor_tests", return_value=True) as run_tests:
                        failed = mutation_audit_verification.mutant_fails_selected_tests(
                            repo=repo,
                            mutmut=["mutmut"],
                            target_file=target,
                            original=b"VALUE = 1\n",
                            mutant="pkg.x__mutmut_1",
                            log=log,
                            test_selection=("tests/test.py",),
                        )
            self.assertTrue(failed)
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
            apply.assert_called_once()
            run_tests.assert_called_once()
            self.assertEqual(apply.call_args.kwargs["repo"], repo)
            self.assertEqual(apply.call_args.kwargs["mutmut"], ["mutmut"])
            self.assertEqual(apply.call_args.kwargs["mutant"], "pkg.x__mutmut_1")
            self.assertIs(apply.call_args.kwargs["log"], log)
            self.assertEqual(run_tests.call_args.kwargs["repo"], repo)
            self.assertEqual(run_tests.call_args.kwargs["mutmut"], ["mutmut"])
            self.assertEqual(run_tests.call_args.kwargs["test_selection"], ("tests/test.py",))
            self.assertIs(run_tests.call_args.kwargs["log"], log)
            self.assertEqual(
                (repo / "verify.log").read_text(encoding="utf-8"),
                "verification killed pkg.x__mutmut_1: rc=1\n",
            )

    def test_mutant_fails_selected_tests_logs_kept_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = repo / "target.py"
            target.write_text("changed\n", encoding="utf-8")
            with (repo / "verify.log").open("w", encoding="utf-8") as log:
                with patch.object(mutation_audit_verification, "apply_mutant", return_value=True):
                    with patch.object(mutation_audit_verification, "run_survivor_tests", return_value=False):
                        failed = mutation_audit_verification.mutant_fails_selected_tests(
                            repo=repo,
                            mutmut=["mutmut"],
                            target_file=target,
                            original=b"original\n",
                            mutant="pkg.x__mutmut_2",
                            log=log,
                            test_selection=("tests/test.py",),
                        )

            self.assertFalse(failed)
            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(
                (repo / "verify.log").read_text(encoding="utf-8"),
                "verification kept pkg.x__mutmut_2: rc=0\n",
            )

    def test_mutant_fails_selected_tests_rejects_apply_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = repo / "target.py"
            target.write_text("original\n", encoding="utf-8")
            with (repo / "verify.log").open("w", encoding="utf-8") as log:
                with patch.object(mutation_audit_verification, "apply_mutant", return_value=False):
                    with patch.object(mutation_audit_verification, "run_survivor_tests") as run_tests:
                        with self.assertRaisesRegex(RuntimeError, "unable to apply mutant"):
                            mutation_audit_verification.mutant_fails_selected_tests(
                                repo=repo,
                                mutmut=["mutmut"],
                                target_file=target,
                                original=b"original\n",
                                mutant="pkg.x__mutmut_1",
                                log=log,
                                test_selection=("tests/test.py",),
                            )

            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            run_tests.assert_not_called()


    def test_verify_mutants_empty_input_skips_file_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            log_path = repo / "verify.log"

            verified = mutation_audit_verification.verify_mutants(
                repo=repo,
                mutmut=["python3", "-m", "mutmut"],
                target_path="missing.py",
                mutant_names=[],
                log_path=log_path,
                test_selection=(),
                configure_target=lambda _repo, _target: _null_context(),
                status_label="survived",
            )

            self.assertEqual(verified, 0)
            self.assertFalse(log_path.exists())

    def test_verify_survivors_delegates_to_named_survivor_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            log_path = repo / "verify.log"
            with patch.object(mutation_audit_verification, "verify_mutants", return_value=2) as verify_mutants:
                configure = lambda _repo, _target: _null_context()
                verified = mutation_audit_verification.verify_survivors(
                    repo=repo,
                    mutmut=["mutmut"],
                    target_path="target.py",
                    survivor_names=["pkg.x__mutmut_1", "pkg.x__mutmut_2"],
                    log_path=log_path,
                    test_selection=("tests/test.py",),
                    configure_target=configure,
                )

        self.assertEqual(verified, 2)
        self.assertEqual(verify_mutants.call_args.kwargs["repo"], repo)
        self.assertEqual(verify_mutants.call_args.kwargs["mutmut"], ["mutmut"])
        self.assertEqual(verify_mutants.call_args.kwargs["target_path"], "target.py")
        self.assertEqual(verify_mutants.call_args.kwargs["log_path"], log_path)
        self.assertEqual(verify_mutants.call_args.kwargs["test_selection"], ("tests/test.py",))
        self.assertIs(verify_mutants.call_args.kwargs["configure_target"], configure)
        self.assertEqual(verify_mutants.call_args.kwargs["status_label"], "survived")
        self.assertEqual(
            verify_mutants.call_args.kwargs["mutant_names"],
            ["pkg.x__mutmut_1", "pkg.x__mutmut_2"],
        )

    def test_mutant_apply_and_survivor_test_subprocess_helpers(self) -> None:
        completed_ok = subprocess.CompletedProcess(["cmd"], 0)
        completed_fail = subprocess.CompletedProcess(["cmd"], 2)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            log = _MemoryTextWriter()
            with patch("subprocess.run", return_value=completed_ok) as run:
                self.assertTrue(
                    mutation_audit_verification.apply_mutant(
                        repo=repo,
                        mutmut=["mutmut"],
                        mutant="pkg.x__mutmut_1",
                        log=log,
                    )
                )
            self.assertEqual(run.call_args.args[0], ["mutmut", "apply", "pkg.x__mutmut_1"])
            self.assertIs(run.call_args.kwargs["cwd"], repo)
            self.assertIs(run.call_args.kwargs["check"], False)
            self.assertIs(run.call_args.kwargs["stdout"], log)
            self.assertIs(run.call_args.kwargs["stderr"], subprocess.STDOUT)
            self.assertIs(run.call_args.kwargs["text"], True)

            with patch("subprocess.run", return_value=subprocess.CompletedProcess(["cmd"], 0)):
                self.assertFalse(
                    mutation_audit_verification.run_survivor_tests(
                        repo=repo,
                        mutmut=["python3", "-m", "mutmut"],
                        test_selection=("tests/test.py",),
                        log=log,
                    )
                )

            with patch("subprocess.run", return_value=subprocess.CompletedProcess(["cmd"], 1)):
                self.assertTrue(
                    mutation_audit_verification.run_survivor_tests(
                        repo=repo,
                        mutmut=["python3", "-m", "mutmut"],
                        test_selection=("tests/test.py",),
                        log=log,
                    )
                )

            with patch("subprocess.run", return_value=completed_fail):
                self.assertFalse(
                    mutation_audit_verification.apply_mutant(
                        repo=repo,
                        mutmut=["mutmut"],
                        mutant="pkg.x__mutmut_2",
                        log=log,
                    )
                )
            self.assertIn("verification apply failed for pkg.x__mutmut_2: rc=2", log.text)

            with patch("subprocess.run", return_value=completed_fail) as run:
                self.assertTrue(
                    mutation_audit_verification.run_survivor_tests(
                        repo=repo,
                        mutmut=["python3", "-m", "mutmut"],
                        test_selection=("tests/test.py",),
                        log=log,
                    )
                )
            self.assertEqual(
                run.call_args.args[0],
                ["python3", "-m", "pytest", "-q", "-k", "not socket", "tests/test.py"],
            )
            self.assertIs(run.call_args.kwargs["cwd"], repo)
            self.assertIs(run.call_args.kwargs["check"], False)
            self.assertIs(run.call_args.kwargs["stdout"], log)
            self.assertIs(run.call_args.kwargs["stderr"], subprocess.STDOUT)
            self.assertIs(run.call_args.kwargs["text"], True)

    def test_verification_rejects_apply_failure_instead_of_counting_survivor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = repo / "target.py"
            target.write_text("original\n", encoding="utf-8")
            log = _MemoryTextWriter()
            with patch.object(mutation_audit_verification, "apply_mutant", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "unable to apply mutant"):
                    mutation_audit_verification.mutant_fails_selected_tests(
                        repo=repo,
                        mutmut=["mutmut"],
                        target_file=target,
                        original=b"original\n",
                        mutant="pkg.x__mutmut_1",
                        log=log,
                        test_selection=("tests/test.py",),
                    )
            self.assertEqual(target.read_bytes(), b"original\n")

    def test_clear_source_bytecode_removes_only_matching_module_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "target.py"
            cache = root / "__pycache__"
            cache.mkdir()
            matching = cache / "target.cpython-311.pyc"
            unrelated = cache / "other.cpython-311.pyc"
            matching.write_bytes(b"cached")
            unrelated.write_bytes(b"cached")

            mutation_audit_verification.clear_source_bytecode(source)

            self.assertFalse(matching.exists())
            self.assertTrue(unrelated.exists())

    def test_python_for_mutmut_command_only_reuses_python_module_mutmut(self) -> None:
        self.assertEqual(
            mutation_audit_verification.python_for_mutmut_command(["/tmp/python", "-m", "mutmut"]),
            "/tmp/python",
        )
        self.assertEqual(
            mutation_audit_verification.python_for_mutmut_command(["/tmp/python", "-m", "mutmut", "run"]),
            "/tmp/python",
        )
        self.assertEqual(
            mutation_audit_verification.python_for_mutmut_command(["/tmp/python", "-m", "pytest"]),
            sys.executable,
        )
        self.assertEqual(
            mutation_audit_verification.python_for_mutmut_command(["mutmut"]),
            sys.executable,
        )

    def test_run_cli_lists_targets_and_uses_selected_defaults(self) -> None:
        with patch.object(mutation_audit.audit_cli, "print_targets", return_value=0) as print_targets:
            result = mutation_audit.main(["--list-targets", "venus_evcharger/energy/numeric.py"])

        self.assertEqual(result, 0)
        print_targets.assert_called_once()
        self.assertEqual(print_targets.call_args.args[0][0].path, "venus_evcharger/energy/numeric.py")

    def test_main_passes_argv_and_module_docstring_to_parser(self) -> None:
        args = argparse.Namespace()
        with patch.object(mutation_audit.audit_cli, "parse_args", return_value=args) as parse_args:
            with patch.object(mutation_audit, "_run_cli", return_value=5) as run_cli:
                self.assertEqual(mutation_audit.main(["target.py"]), 5)

        parse_args.assert_called_once_with(["target.py"], description=mutation_audit.__doc__)
        run_cli.assert_called_once_with(args)

    def test_run_cli_executes_selected_targets_and_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            out_dir = root / "out"
            target = mutation_audit_support.MutationTarget("target.py")
            result = mutation_audit_results.TargetResult(
                path="target.py",
                status="ok",
                duration_s=1.0,
                run_returncode=0,
                results_returncode=0,
                log_path="target.log",
                results_path="target.results",
                counts=mutation_audit_results.zero_counts(),
            )
            args = argparse.Namespace(
                targets=["target.py"],
                list_targets=False,
                out_dir="/tmp/requested-out",
                timeout_s=12.5,
                reuse_cache=True,
                verify_survivors=True,
                no_fail=False,
            )

            with patch.object(mutation_audit.audit_cli, "selected_targets", return_value=[target]) as selected_targets:
                with patch.object(mutation_audit.audit_cli, "repo_dir", return_value=repo) as repo_dir:
                    with patch.object(mutation_audit.audit_cli, "mutmut_command", return_value=["mutmut"]) as mutmut_command:
                        with patch.object(mutation_audit.audit_cli, "output_dir", return_value=out_dir) as output_dir:
                            with patch.object(Path, "mkdir", autospec=True) as mkdir:
                                with patch.object(mutation_audit.audit_support, "mutmut_audit_lock", return_value=_null_context()) as lock:
                                    with patch.object(mutation_audit.audit_execution, "run_target", return_value=result) as run_target:
                                        with patch.object(mutation_audit.audit_results, "write_summary") as write_summary:
                                            with patch.object(mutation_audit.audit_results, "print_summary") as print_summary:
                                                with patch.object(
                                                    mutation_audit.audit_results,
                                                    "exit_code",
                                                    return_value=7,
                                                ) as exit_code:
                                                    status = mutation_audit._run_cli(args)

            self.assertEqual(status, 7)
            selected_targets.assert_called_once_with(["target.py"])
            repo_dir.assert_called_once_with()
            mutmut_command.assert_called_once_with(repo)
            output_dir.assert_called_once_with(repo, "/tmp/requested-out")
            mkdir.assert_called_once_with(out_dir, parents=True, exist_ok=True)
            lock.assert_called_once_with(repo)
            run_target.assert_called_once()
            run_kwargs = run_target.call_args.kwargs
            self.assertEqual(run_kwargs["repo"], repo)
            self.assertEqual(run_kwargs["out_dir"], out_dir)
            self.assertEqual(run_kwargs["mutmut"], ["mutmut"])
            self.assertEqual(run_kwargs["target"], target)
            self.assertEqual(run_kwargs["options"].timeout_s, 12.5)
            self.assertTrue(run_kwargs["options"].reuse_cache)
            self.assertTrue(run_kwargs["options"].verify_survivors)
            write_summary.assert_called_once_with([result], out_dir / "summary.json")
            print_summary.assert_called_once_with([result], out_dir / "summary.json")
            exit_code.assert_called_once_with([result], no_fail=False)

    def test_run_cli_reports_missing_mutmut_before_creating_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            out_dir = Path(temp_dir) / "out"
            args = argparse.Namespace(
                targets=[],
                list_targets=False,
                out_dir=None,
                timeout_s=1.0,
                reuse_cache=False,
                verify_survivors=False,
                no_fail=True,
            )
            stderr = StringIO()
            with patch.object(mutation_audit.audit_cli, "selected_targets", return_value=[]):
                with patch.object(mutation_audit.audit_cli, "repo_dir", return_value=repo) as repo_dir:
                    with patch.object(mutation_audit.audit_cli, "mutmut_command", return_value=None) as mutmut_command:
                        with patch.object(mutation_audit.audit_cli, "output_dir", return_value=out_dir) as output_dir:
                            with redirect_stdout(StringIO()):
                                with patch.object(sys, "stderr", stderr):
                                    status = mutation_audit._run_cli(args)

            self.assertEqual(status, 2)
            self.assertEqual(
                stderr.getvalue(),
                "mutmut is required. Install it with: python3 -m pip install mutmut\n",
            )
            repo_dir.assert_called_once_with()
            mutmut_command.assert_called_once_with(repo)
            output_dir.assert_not_called()
            self.assertFalse(out_dir.exists())

    def test_print_helpers_emit_target_and_summary_lines(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                mutation_audit_cli.print_targets(
                    [
                        mutation_audit_support.MutationTarget("a.py"),
                        mutation_audit_support.MutationTarget("b.py"),
                    ]
                ),
                0,
            )

        self.assertEqual(output.getvalue().splitlines(), ["a.py", "b.py"])

        output = StringIO()
        result = mutation_audit_results.TargetResult(
            path="target.py",
            status="ok",
            duration_s=1.25,
            run_returncode=0,
            results_returncode=0,
            log_path="/tmp/log",
            results_path="/tmp/results",
            counts={"killed": 1, "survived": 0},
        )
        with redirect_stdout(output):
            mutation_audit_results.print_summary([result], Path("/tmp/summary.json"))

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "Mutation audit summary: /tmp/summary.json",
                "- ok                   1.2s target.py killed=1 survived=0",
            ],
        )

    def test_result_helpers_shape_runtime_and_summary_contracts(self) -> None:
        run = mutation_audit_support.TargetMutmutRun(
            run_result=subprocess.CompletedProcess(["mutmut", "run"], 0),
            results_result=subprocess.CompletedProcess(["mutmut", "results"], 0),
            result_text="1 killed\n",
            log_text="",
        )
        artifacts = mutation_audit_support.TargetArtifacts(Path("/tmp/log"), Path("/tmp/results"))
        target = mutation_audit_support.MutationTarget("target.py")
        counts = dict.fromkeys(mutation_audit_support.RESULT_WORDS, 0)
        counts["killed"] = 1
        with patch.object(mutation_audit_results, "duration_since", return_value=2.5):
            result = mutation_audit_results.runtime_result(
                target=target,
                started=1.0,
                artifacts=artifacts,
                run=run,
                counts=counts,
            )

        self.assertEqual(result.path, "target.py")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.duration_s, 2.5)
        self.assertEqual(result.run_returncode, 0)
        self.assertEqual(result.results_returncode, 0)
        self.assertEqual(result.log_path, "/tmp/log")
        self.assertEqual(result.results_path, "/tmp/results")
        self.assertEqual(
            result.counts,
            {
                "killed": 1,
                "survived": 0,
                "timeout": 0,
                "suspicious": 0,
                "skipped": 0,
                "no_tests": 0,
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "summary.json"
            mutation_audit_results.write_summary([result], summary_path)
            summary_text = summary_path.read_text(encoding="utf-8")

        self.assertEqual(
            summary_text,
            """[
  {
    "counts": {
      "killed": 1,
      "no_tests": 0,
      "skipped": 0,
      "survived": 0,
      "suspicious": 0,
      "timeout": 0
    },
    "duration_s": 2.5,
    "log_path": "/tmp/log",
    "path": "target.py",
    "results_path": "/tmp/results",
    "results_returncode": 0,
    "run_returncode": 0,
    "status": "ok"
  }
]
""",
        )
        parsed = json.loads(summary_text)
        self.assertEqual(parsed[0]["path"], "target.py")
        self.assertEqual(parsed[0]["counts"]["killed"], 1)

        failing_run = mutation_audit_support.TargetMutmutRun(
            run_result=subprocess.CompletedProcess(["mutmut", "run"], 1),
            results_result=subprocess.CompletedProcess(["mutmut", "results"], 0),
            result_text="",
            log_text="mutmut could not find any test case for any mutant",
        )
        empty_counts = dict.fromkeys(mutation_audit_support.RESULT_WORDS, 0)
        failing_result = mutation_audit_results.runtime_result(
            target=target,
            started=1.0,
            artifacts=artifacts,
            run=failing_run,
            counts=empty_counts,
        )
        self.assertEqual(failing_result.status, "needs_attention")

    def test_result_skip_helpers_and_duration_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = mutation_audit_support.MutationTarget("constants.py")
            log_path = root / "target.log"
            results_path = root / "target.results"
            with patch.object(mutation_audit_results, "duration_since", return_value=4.0):
                not_applicable = mutation_audit_results.not_applicable_result(
                    target=target,
                    started=1.0,
                    log_path=log_path,
                    results_path=results_path,
                    reason="constant-only",
                )

            expected_skip = "Skipping mutation audit for constants.py: constant-only\n"
            self.assertEqual(not_applicable.path, "constants.py")
            self.assertEqual(not_applicable.status, "not_applicable")
            self.assertEqual(not_applicable.duration_s, 4.0)
            self.assertEqual(not_applicable.run_returncode, 0)
            self.assertEqual(not_applicable.results_returncode, 0)
            self.assertEqual(not_applicable.log_path, str(log_path))
            self.assertEqual(not_applicable.results_path, str(results_path))
            self.assertEqual(
                not_applicable.counts,
                {
                    "killed": 0,
                    "survived": 0,
                    "timeout": 0,
                    "suspicious": 0,
                    "skipped": 1,
                    "no_tests": 0,
                },
            )
            self.assertEqual(log_path.read_text(encoding="utf-8"), expected_skip)
            self.assertEqual(results_path.read_text(encoding="utf-8"), expected_skip)

            run = mutation_audit_support.TargetMutmutRun(
                run_result=subprocess.CompletedProcess(["mutmut"], 3),
                results_result=subprocess.CompletedProcess(["mutmut"], 4),
                result_text="",
                log_text="",
            )
            artifacts = mutation_audit_support.TargetArtifacts(log_path=root / "no.log", results_path=root / "no.results")
            artifacts.log_path.write_text("log", encoding="utf-8")
            artifacts.results_path.write_text("results", encoding="utf-8")
            with patch.object(mutation_audit_results, "duration_since", return_value=5.0):
                no_mutants = mutation_audit_results.no_generated_mutants_result(
                    target=target,
                    started=1.0,
                    artifacts=artifacts,
                    run=run,
                )

            attention_skip = "\nSkipping mutation attention for constants.py: mutmut generated no mutant keys for this target\n"
            self.assertEqual(no_mutants.path, "constants.py")
            self.assertEqual(no_mutants.status, "not_applicable")
            self.assertEqual(no_mutants.duration_s, 5.0)
            self.assertEqual(no_mutants.run_returncode, 3)
            self.assertEqual(no_mutants.results_returncode, 4)
            self.assertEqual(no_mutants.log_path, str(artifacts.log_path))
            self.assertEqual(no_mutants.results_path, str(artifacts.results_path))
            self.assertEqual(
                no_mutants.counts,
                {
                    "killed": 0,
                    "survived": 0,
                    "timeout": 0,
                    "suspicious": 0,
                    "skipped": 1,
                    "no_tests": 0,
                },
            )
            self.assertEqual(artifacts.log_path.read_text(encoding="utf-8"), "log" + attention_skip)
            self.assertEqual(artifacts.results_path.read_text(encoding="utf-8"), "results" + attention_skip)

        append_path = Path("/tmp/append.log")
        append_writers = {append_path: _MemoryTextWriter()}
        with patch.object(Path, "open", autospec=True, side_effect=_strict_append_open(append_writers)):
            mutation_audit_results.append_skip_reason(append_path, "target.py", "because")

        self.assertEqual(append_writers[append_path].text, "\nSkipping mutation attention for target.py: because\n")

        with patch.object(mutation_audit_results.time, "monotonic", return_value=12.3456):
            self.assertEqual(mutation_audit_results.duration_since(10.0), 2.346)

    def test_result_counts_and_exit_contracts(self) -> None:
        counts = mutation_audit_results.zero_counts()
        counts["killed"] = 3
        fresh_counts = mutation_audit_results.zero_counts()

        self.assertEqual(set(counts), set(mutation_audit_support.RESULT_WORDS))
        self.assertEqual(fresh_counts["killed"], 0)
        self.assertEqual(counts["killed"], 3)

        ok = mutation_audit_results.TargetResult("ok.py", "ok", 0.0, 0, 0, "log", "results", fresh_counts)
        bad = mutation_audit_results.TargetResult("bad.py", "needs_attention", 0.0, 1, 0, "log", "results", counts)
        self.assertEqual(mutation_audit_results.exit_code([ok], no_fail=False), 0)
        self.assertEqual(mutation_audit_results.exit_code([bad], no_fail=False), 1)
        self.assertEqual(mutation_audit_results.exit_code([bad], no_fail=True), 0)

    def test_output_dir_and_worktree_cleanup_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.assertEqual(mutation_audit_cli.output_dir(repo, "/tmp/out"), Path("/tmp/out"))
            self.assertEqual(mutation_audit_cli.output_dir(repo, "~/out").name, "out")
            (repo / mutation_audit_execution.MUTMUT_CACHE).mkdir()
            (repo / mutation_audit_execution.MUTMUT_WORKTREE).mkdir()

            mutation_audit_execution.clear_mutmut_worktree(repo, reuse_cache=False)

            self.assertFalse((repo / mutation_audit_execution.MUTMUT_CACHE).exists())
            self.assertFalse((repo / mutation_audit_execution.MUTMUT_WORKTREE).exists())
            mutation_audit_execution.clear_mutmut_worktree(repo, reuse_cache=True)

    def test_worktree_cleanup_unlinks_symlinks_without_removing_their_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            cache_target = repo / "external-cache"
            worktree_target = repo / "external-worktree"
            cache_target.mkdir()
            worktree_target.mkdir()
            cache_link = repo / mutation_audit_execution.MUTMUT_CACHE
            worktree_link = repo / mutation_audit_execution.MUTMUT_WORKTREE
            cache_link.symlink_to(cache_target, target_is_directory=True)
            worktree_link.symlink_to(worktree_target, target_is_directory=True)

            mutation_audit_execution.clear_mutmut_worktree(repo, reuse_cache=False)

            self.assertFalse(cache_link.is_symlink())
            self.assertFalse(worktree_link.is_symlink())
            self.assertTrue(cache_target.is_dir())
            self.assertTrue(worktree_target.is_dir())

    def test_worktree_cleanup_removes_legacy_regular_file_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            cache_path = repo / mutation_audit_execution.MUTMUT_CACHE
            worktree_path = repo / mutation_audit_execution.MUTMUT_WORKTREE
            cache_path.write_text("cache", encoding="utf-8")
            worktree_path.write_text("worktree", encoding="utf-8")

            mutation_audit_execution.clear_mutmut_worktree(repo, reuse_cache=False)

            self.assertFalse(cache_path.exists())
            self.assertFalse(worktree_path.exists())

    def test_execution_artifact_and_cleanup_contracts(self) -> None:
        repo = Path("/repo")
        target = mutation_audit_support.MutationTarget("scripts/dev/run_mutation_audit.py")
        artifacts = mutation_audit_execution.artifacts_for_target(Path("/out"), target)

        self.assertEqual(artifacts.log_path, Path("/out/scripts_dev_run_mutation_audit.py.log"))
        self.assertEqual(artifacts.results_path, Path("/out/scripts_dev_run_mutation_audit.py.results.txt"))

        with patch.object(mutation_audit_execution.shutil, "rmtree") as rmtree:
            mutation_audit_execution.clear_mutmut_worktree(repo, reuse_cache=False)
        self.assertEqual(
            rmtree.call_args_list,
            [
                unittest.mock.call(repo / mutation_audit_execution.MUTMUT_CACHE, ignore_errors=True),
                unittest.mock.call(repo / mutation_audit_execution.MUTMUT_WORKTREE, ignore_errors=True),
            ],
        )

        with patch.object(mutation_audit_execution.shutil, "rmtree") as rmtree:
            mutation_audit_execution.clear_mutmut_worktree(repo, reuse_cache=True)
        rmtree.assert_not_called()

    def test_clear_target_bytecode_removes_only_target_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            package = repo / "package"
            cache_dir = package / "__pycache__"
            cache_dir.mkdir(parents=True)
            (package / "module.py").write_text("value = 1\n", encoding="utf-8")
            target_cache = cache_dir / "module.cpython-314.pyc"
            optimized_cache = cache_dir / "module.cpython-314.opt-1.pyc"
            sibling_cache = cache_dir / "sibling.cpython-314.pyc"
            for path in (target_cache, optimized_cache, sibling_cache):
                path.write_bytes(b"bytecode")

            mutation_audit_execution.clear_target_bytecode(repo, "package/module.py")

            self.assertFalse(target_cache.exists())
            self.assertFalse(optimized_cache.exists())
            self.assertTrue(sibling_cache.exists())
            mutation_audit_execution.clear_target_bytecode(repo, "package/data.json")

    def test_run_target_clears_bytecode_after_survivor_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = mutation_audit_support.MutationTarget("package/module.py")
            target_file = repo / target.path
            target_file.parent.mkdir()
            target_file.write_text("VALUE = 1\n", encoding="utf-8")
            run = mutation_audit_support.TargetMutmutRun(
                run_result=subprocess.CompletedProcess(["mutmut"], 0),
                results_result=subprocess.CompletedProcess(["mutmut"], 0),
                result_text="1 survived\n",
                log_text="",
            )
            counts = mutation_audit_results.zero_counts()
            counts["survived"] = 1
            events: list[str] = []

            with (
                patch.object(mutation_audit_execution.audit_support, "not_applicable_mutation_target", return_value=None),
                patch.object(mutation_audit_execution, "clear_mutmut_worktree"),
                patch.object(mutation_audit_execution, "run_mutmut_for_target", return_value=run),
                patch.object(mutation_audit_execution.audit_support, "generated_no_mutants", return_value=False),
                patch.object(
                    mutation_audit_execution,
                    "target_counts",
                    side_effect=lambda **_kwargs: events.append("verify") or counts,
                ),
                patch.object(
                    mutation_audit_execution.audit_results,
                    "runtime_result",
                    return_value=unittest.mock.sentinel.result,
                ),
                patch.object(
                    mutation_audit_execution,
                    "clear_target_bytecode",
                    side_effect=lambda *_args: events.append("cleanup"),
                ),
            ):
                result = mutation_audit_execution.run_target(
                    repo=repo,
                    out_dir=repo / "out",
                    mutmut=["mutmut"],
                    target=target,
                    options=mutation_audit_execution.TargetRunOptions(10.0, False, True),
                )

        self.assertIs(result, unittest.mock.sentinel.result)
        self.assertEqual(events, ["verify", "cleanup"])

    def test_run_target_restores_source_before_counting_and_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = mutation_audit_support.MutationTarget("package/target.py")
            target_file = repo / target.path
            target_file.parent.mkdir()
            target_file.write_text("VALUE = 'original'\n", encoding="utf-8")
            run = mutation_audit_support.TargetMutmutRun(
                run_result=subprocess.CompletedProcess(["mutmut"], 0),
                results_result=subprocess.CompletedProcess(["mutmut"], 0),
                result_text="1 killed\n",
                log_text="",
            )
            counts = mutation_audit_results.zero_counts()
            counts["killed"] = 1

            def dirty_run(**_kwargs: object) -> mutation_audit_support.TargetMutmutRun:
                target_file.write_text("VALUE = 'mutant'\n", encoding="utf-8")
                return run

            def assert_clean_counts(**_kwargs: object) -> dict[str, int]:
                self.assertEqual(target_file.read_text(encoding="utf-8"), "VALUE = 'original'\n")
                target_file.write_text("VALUE = 'verification-mutant'\n", encoding="utf-8")
                return counts

            with (
                patch.object(mutation_audit_execution.audit_support, "not_applicable_mutation_target", return_value=None),
                patch.object(mutation_audit_execution, "clear_mutmut_worktree"),
                patch.object(mutation_audit_execution, "run_mutmut_for_target", side_effect=dirty_run),
                patch.object(mutation_audit_execution.audit_support, "generated_no_mutants", return_value=False),
                patch.object(mutation_audit_execution, "target_counts", side_effect=assert_clean_counts),
            ):
                result = mutation_audit_execution.run_target(
                    repo=repo,
                    out_dir=repo / "out",
                    mutmut=["mutmut"],
                    target=target,
                    options=mutation_audit_execution.TargetRunOptions(10.0, False, True),
                )

            self.assertEqual(result.counts["killed"], 1)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "VALUE = 'original'\n")

    def test_read_artifact_uses_replace_errors_and_empty_missing_file(self) -> None:
        path = Path("/tmp/artifact.log")

        def read_text(read_path: Path, *args: object, **kwargs: object) -> str:
            self.assertEqual(read_path, path)
            self.assertFalse(args)
            self.assertEqual(kwargs, {"errors": "replace"})
            return "payload"

        with patch.object(Path, "exists", autospec=True, return_value=True) as exists:
            with patch.object(Path, "read_text", autospec=True, side_effect=read_text) as read:
                self.assertEqual(mutation_audit_execution.read_artifact(path), "payload")
        exists.assert_called_once_with(path)
        read.assert_called_once()

        with patch.object(Path, "exists", autospec=True, return_value=False):
            with patch.object(Path, "read_text", autospec=True) as read:
                self.assertEqual(mutation_audit_execution.read_artifact(path), "")
        read.assert_not_called()

    def test_repo_dir_points_to_project_root(self) -> None:
        self.assertEqual(mutation_audit_cli.repo_dir(), Path(__file__).resolve().parents[1])
        self.assertTrue((mutation_audit_cli.repo_dir() / "scripts" / "dev" / "run_mutation_audit.py").exists())

    def test_not_applicable_and_no_generated_mutant_results_write_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = mutation_audit_support.MutationTarget("constants.py")
            log_path = root / "target.log"
            results_path = root / "target.results"

            not_applicable = mutation_audit_results.not_applicable_result(
                target=target,
                started=0.0,
                log_path=log_path,
                results_path=results_path,
                reason="constant-only",
            )

            self.assertEqual(not_applicable.status, "not_applicable")
            self.assertEqual(not_applicable.counts["skipped"], 1)
            self.assertIn("constant-only", log_path.read_text(encoding="utf-8"))

            artifacts = mutation_audit_support.TargetArtifacts(log_path=root / "no.log", results_path=root / "no.results")
            artifacts.log_path.write_text("log", encoding="utf-8")
            artifacts.results_path.write_text("results", encoding="utf-8")
            no_mutants = mutation_audit_results.no_generated_mutants_result(
                target=target,
                started=0.0,
                artifacts=artifacts,
                run=mutation_audit_support.TargetMutmutRun(
                    run_result=subprocess.CompletedProcess(["mutmut"], 0),
                    results_result=subprocess.CompletedProcess(["mutmut"], 0),
                    result_text="",
                    log_text="",
                ),
            )

            self.assertEqual(no_mutants.status, "not_applicable")
            self.assertEqual(no_mutants.counts["skipped"], 1)
            self.assertIn("generated no mutant keys", artifacts.log_path.read_text(encoding="utf-8"))

    def test_target_counts_marks_and_verifies_attention_counts(self) -> None:
        run = mutation_audit_support.TargetMutmutRun(
            run_result=subprocess.CompletedProcess(["mutmut"], 1),
            results_result=subprocess.CompletedProcess(["mutmut"], 0),
            result_text="pkg.x__mutmut_1: survived\npkg.x__mutmut_2: no tests\n",
            log_text="",
        )
        command = mutation_audit_support.MutationCommandContext(Path("."), ["mutmut"])
        target = mutation_audit_support.MutationTarget("target.py")
        artifacts = mutation_audit_support.TargetArtifacts(Path("/tmp/log"), Path("/tmp/results"))
        with patch.object(mutation_audit_execution.audit_verification, "verify_survivors", return_value=1) as verify_survivors:
            with patch.object(mutation_audit_execution.audit_verification, "verify_mutants", return_value=1) as verify_mutants:
                counts = mutation_audit_execution.target_counts(
                    command=command,
                    target=target,
                    run=run,
                    artifacts=artifacts,
                    verify_survivors=True,
                )

        self.assertEqual(counts["survived"], 0)
        self.assertEqual(counts["no_tests"], 0)
        self.assertEqual(counts["killed"], 2)
        verify_survivors.assert_called_once()
        verify_mutants.assert_called_once()

        mapping_run = mutation_audit_support.TargetMutmutRun(
            run_result=subprocess.CompletedProcess(["mutmut"], 1),
            results_result=subprocess.CompletedProcess(["mutmut"], 0),
            result_text="",
            log_text="mutmut could not find any test case for any mutant",
        )
        with patch.object(mutation_audit_execution, "verify_target_attention_counts") as verify:
            counts = mutation_audit_execution.target_counts(
                command=command,
                target=target,
                run=mapping_run,
                artifacts=artifacts,
                verify_survivors=False,
            )

        self.assertEqual(counts["no_tests"], 1)
        verify.assert_not_called()

        preexisting_counts = mutation_audit_results.zero_counts()
        preexisting_counts["no_tests"] = 3
        with patch.object(mutation_audit_execution.audit_support, "parse_counts", return_value=preexisting_counts):
            with patch.object(mutation_audit_execution, "run_has_no_test_mapping", return_value=True):
                counts = mutation_audit_execution.target_counts(
                    command=command,
                    target=target,
                    run=mapping_run,
                    artifacts=artifacts,
                    verify_survivors=False,
                )
        self.assertEqual(counts["no_tests"], 4)

    def test_verify_attention_helpers_pass_exact_contracts_and_move_counts(self) -> None:
        command = mutation_audit_support.MutationCommandContext(Path("/repo"), ["python", "-m", "mutmut"])
        target = mutation_audit_support.MutationTarget("target.py")
        artifacts = mutation_audit_support.TargetArtifacts(Path("/tmp/target.log"), Path("/tmp/target.results"))
        run = mutation_audit_support.TargetMutmutRun(
            run_result=subprocess.CompletedProcess(["mutmut"], 1),
            results_result=subprocess.CompletedProcess(["mutmut"], 0),
            result_text="pkg.x__mutmut_1: survived\npkg.x__mutmut_2: no tests\n",
            log_text="",
        )
        counts = mutation_audit_results.zero_counts()
        counts["killed"] = 1
        counts["survived"] = 3
        counts["no_tests"] = 2

        with patch.object(mutation_audit_execution.audit_config, "test_selection_for_target", return_value=("tests/a.py",)) as selection:
            with patch.object(mutation_audit_execution.audit_config, "mutmut_config_for_target") as config:
                with patch.object(mutation_audit_execution.audit_support, "mutant_names", return_value=["pkg.x__mutmut_1"]) as names:
                    with patch.object(
                        mutation_audit_execution.audit_verification,
                        "verify_survivors",
                        return_value=2,
                    ) as verify_survivors:
                        mutation_audit_execution.verify_survived_mutants(
                            command=command,
                            target=target,
                            run=run,
                            artifacts=artifacts,
                            counts=counts,
                        )

        self.assertEqual(counts["survived"], 1)
        self.assertEqual(counts["killed"], 3)
        selection.assert_called_once_with("target.py")
        names.assert_called_once_with(run.result_text, "survived")
        verify_survivors.assert_called_once_with(
            repo=Path("/repo"),
            mutmut=["python", "-m", "mutmut"],
            target_path="target.py",
            survivor_names=["pkg.x__mutmut_1"],
            log_path=Path("/tmp/target.log"),
            test_selection=("tests/a.py",),
            configure_target=config,
        )

        with patch.object(mutation_audit_execution.audit_config, "test_selection_for_target", return_value=("tests/b.py",)) as selection:
            with patch.object(mutation_audit_execution.audit_config, "mutmut_config_for_target") as config:
                with patch.object(mutation_audit_execution.audit_support, "mutant_names", return_value=["pkg.x__mutmut_2"]) as names:
                    with patch.object(
                        mutation_audit_execution.audit_verification,
                        "verify_mutants",
                        return_value=1,
                    ) as verify_mutants:
                        mutation_audit_execution.verify_no_test_mutants(
                            command=command,
                            target=target,
                            run=run,
                            artifacts=artifacts,
                            counts=counts,
                        )

        self.assertEqual(counts["no_tests"], 1)
        self.assertEqual(counts["killed"], 4)
        selection.assert_called_once_with("target.py")
        names.assert_called_once_with(run.result_text, "no tests")
        verify_mutants.assert_called_once_with(
            repo=Path("/repo"),
            mutmut=["python", "-m", "mutmut"],
            target_path="target.py",
            mutant_names=["pkg.x__mutmut_2"],
            log_path=Path("/tmp/target.log"),
            test_selection=("tests/b.py",),
            configure_target=config,
            status_label="no-tests",
        )

    def test_run_mutmut_for_target_captures_log_and_result_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            artifacts = mutation_audit_support.TargetArtifacts(repo / "target.log", repo / "target.results")
            artifacts.log_path.write_text("logged", encoding="utf-8")
            artifacts.results_path.write_text("results", encoding="utf-8")
            with patch.object(mutation_audit_config, "mutmut_config_for_target", return_value=_null_context()) as config:
                with patch.object(
                    mutation_audit_process,
                    "run_logged",
                    return_value=subprocess.CompletedProcess(["mutmut", "run"], 0),
                ) as run_logged:
                    with patch.object(
                        mutation_audit_process,
                        "capture_results",
                        return_value=subprocess.CompletedProcess(["mutmut", "results"], 0),
                    ) as capture_results:
                        result = mutation_audit_execution.run_mutmut_for_target(
                            command=mutation_audit_support.MutationCommandContext(repo, ["mutmut"]),
                            target=mutation_audit_support.MutationTarget("target.py"),
                            artifacts=artifacts,
                            timeout_s=3.0,
                        )

        self.assertEqual(result.log_text, "logged")
        self.assertEqual(result.result_text, "results")
        self.assertEqual(result.run_result.returncode, 0)
        self.assertEqual(result.results_result.returncode, 0)
        config.assert_called_once_with(repo, "target.py")
        run_logged.assert_called_once_with(
            ["mutmut", "run"],
            cwd=repo,
            log_path=artifacts.log_path,
            timeout_s=3.0,
        )
        capture_results.assert_called_once_with(
            mutmut=["mutmut"],
            cwd=repo,
            results_path=artifacts.results_path,
        )

        with patch.object(mutation_audit_config, "mutmut_config_for_target", return_value=_null_context()):
            with patch.object(
                mutation_audit_process,
                "run_logged",
                return_value=subprocess.CompletedProcess(["mutmut", "run"], 0),
            ):
                with patch.object(
                    mutation_audit_process,
                    "capture_results",
                    return_value=subprocess.CompletedProcess(["mutmut", "results"], 0),
                ):
                    with patch.object(mutation_audit_execution, "read_artifact", side_effect=["result-text", "log-text"]) as read:
                        result = mutation_audit_execution.run_mutmut_for_target(
                            command=mutation_audit_support.MutationCommandContext(repo, ["mutmut"]),
                            target=mutation_audit_support.MutationTarget("target.py"),
                            artifacts=artifacts,
                            timeout_s=3.0,
                        )

        self.assertEqual(result.result_text, "result-text")
        self.assertEqual(result.log_text, "log-text")
        self.assertEqual(
            read.call_args_list,
            [
                unittest.mock.call(artifacts.results_path),
                unittest.mock.call(artifacts.log_path),
            ],
        )

    def test_run_target_routes_all_result_paths_with_exact_contracts(self) -> None:
        repo = Path("/repo")
        out_dir = Path("/out")
        target = mutation_audit_support.MutationTarget("target.py")
        artifacts = mutation_audit_support.TargetArtifacts(Path("/out/target.log"), Path("/out/target.results"))
        options = mutation_audit_execution.TargetRunOptions(timeout_s=9.0, reuse_cache=True, verify_survivors=True)
        skipped_result = mutation_audit_results.TargetResult(
            "target.py",
            "not_applicable",
            0.0,
            0,
            0,
            str(artifacts.log_path),
            str(artifacts.results_path),
            mutation_audit_results.zero_counts(),
        )

        with patch.object(mutation_audit_execution.time, "monotonic", return_value=123.0):
            with patch.object(mutation_audit_execution, "artifacts_for_target", return_value=artifacts) as artifact_factory:
                with patch.object(
                    mutation_audit_execution.audit_support,
                    "not_applicable_mutation_target",
                    return_value="constant-only",
                ) as not_applicable:
                    with patch.object(
                        mutation_audit_execution.audit_results,
                        "not_applicable_result",
                        return_value=skipped_result,
                    ) as not_applicable_result:
                        result = mutation_audit_execution.run_target(
                            repo=repo,
                            out_dir=out_dir,
                            mutmut=["mutmut"],
                            target=target,
                            options=options,
                        )

        self.assertIs(result, skipped_result)
        artifact_factory.assert_called_once_with(out_dir, target)
        not_applicable.assert_called_once_with(repo, "target.py")
        not_applicable_result.assert_called_once_with(
            target=target,
            started=123.0,
            log_path=artifacts.log_path,
            results_path=artifacts.results_path,
            reason="constant-only",
        )

        run = mutation_audit_support.TargetMutmutRun(
            run_result=subprocess.CompletedProcess(["mutmut"], 0),
            results_result=subprocess.CompletedProcess(["mutmut"], 0),
            result_text="",
            log_text="",
        )
        no_mutants_result = mutation_audit_results.TargetResult(
            "target.py",
            "not_applicable",
            0.0,
            0,
            0,
            str(artifacts.log_path),
            str(artifacts.results_path),
            mutation_audit_results.zero_counts(),
        )
        with patch.object(Path, "read_bytes", autospec=True, return_value=b"source"):
            with patch.object(Path, "write_bytes", autospec=True):
                with patch.object(mutation_audit_execution.time, "monotonic", return_value=234.0):
                    with patch.object(mutation_audit_execution, "artifacts_for_target", return_value=artifacts):
                        with patch.object(mutation_audit_execution.audit_support, "not_applicable_mutation_target", return_value=None):
                            with patch.object(mutation_audit_execution, "clear_mutmut_worktree") as clear:
                                with patch.object(mutation_audit_execution, "run_mutmut_for_target", return_value=run) as run_mutmut:
                                    with patch.object(
                                        mutation_audit_execution.audit_support,
                                        "generated_no_mutants",
                                        return_value=True,
                                    ) as generated_no_mutants:
                                        with patch.object(
                                            mutation_audit_execution.audit_results,
                                            "no_generated_mutants_result",
                                            return_value=no_mutants_result,
                                        ) as no_generated_result:
                                            result = mutation_audit_execution.run_target(
                                                repo=repo,
                                                out_dir=out_dir,
                                                mutmut=["mutmut"],
                                                target=target,
                                                options=options,
                                            )

        self.assertIs(result, no_mutants_result)
        clear.assert_called_once_with(repo, reuse_cache=True)
        run_mutmut.assert_called_once_with(command=unittest.mock.ANY, target=target, artifacts=artifacts, timeout_s=9.0)
        command = run_mutmut.call_args.kwargs["command"]
        self.assertEqual(command.repo, repo)
        self.assertEqual(command.mutmut, ["mutmut"])
        generated_no_mutants.assert_called_once_with(repo, "target.py")
        no_generated_result.assert_called_once_with(target=target, started=234.0, artifacts=artifacts, run=run)

        counts = mutation_audit_results.zero_counts()
        counts["killed"] = 1
        runtime_result = mutation_audit_results.TargetResult(
            "target.py",
            "ok",
            0.0,
            0,
            0,
            str(artifacts.log_path),
            str(artifacts.results_path),
            counts,
        )
        with patch.object(Path, "read_bytes", autospec=True, return_value=b"source"):
            with patch.object(Path, "write_bytes", autospec=True):
                with patch.object(mutation_audit_execution.time, "monotonic", return_value=345.0):
                    with patch.object(mutation_audit_execution, "artifacts_for_target", return_value=artifacts):
                        with patch.object(mutation_audit_execution.audit_support, "not_applicable_mutation_target", return_value=None):
                            with patch.object(mutation_audit_execution, "clear_mutmut_worktree"):
                                with patch.object(mutation_audit_execution, "run_mutmut_for_target", return_value=run):
                                    with patch.object(mutation_audit_execution.audit_support, "generated_no_mutants", return_value=False):
                                        with patch.object(mutation_audit_execution, "target_counts", return_value=counts) as target_counts:
                                            with patch.object(
                                                mutation_audit_execution.audit_results,
                                                "runtime_result",
                                                return_value=runtime_result,
                                            ) as runtime:
                                                result = mutation_audit_execution.run_target(
                                                    repo=repo,
                                                    out_dir=out_dir,
                                                    mutmut=["mutmut"],
                                                    target=target,
                                                    options=options,
                                                )

        self.assertIs(result, runtime_result)
        target_counts.assert_called_once()
        self.assertEqual(target_counts.call_args.kwargs["target"], target)
        command = target_counts.call_args.kwargs["command"]
        self.assertEqual(command.repo, repo)
        self.assertEqual(command.mutmut, ["mutmut"])
        self.assertIs(target_counts.call_args.kwargs["run"], run)
        self.assertEqual(target_counts.call_args.kwargs["artifacts"], artifacts)
        self.assertTrue(target_counts.call_args.kwargs["verify_survivors"])
        runtime.assert_called_once_with(target=target, started=345.0, artifacts=artifacts, run=run, counts=counts)

    def test_run_target_handles_not_applicable_and_normal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            out_dir = repo / "out"
            out_dir.mkdir()
            target = mutation_audit_support.MutationTarget("target.py")
            (repo / target.path).write_text("VALUE = 1\n", encoding="utf-8")
            options = mutation_audit_execution.TargetRunOptions(
                timeout_s=1.0,
                reuse_cache=False,
                verify_survivors=False,
            )
            with patch.object(mutation_audit_support, "not_applicable_mutation_target", return_value="skip"):
                skipped = mutation_audit_execution.run_target(
                    repo=repo,
                    out_dir=out_dir,
                    mutmut=["mutmut"],
                    target=target,
                    options=options,
                )
            self.assertEqual(skipped.status, "not_applicable")

            run = mutation_audit_support.TargetMutmutRun(
                run_result=subprocess.CompletedProcess(["mutmut"], 0),
                results_result=subprocess.CompletedProcess(["mutmut"], 0),
                result_text="1 killed\n",
                log_text="",
            )
            with patch.object(mutation_audit_support, "not_applicable_mutation_target", return_value=None):
                with patch.object(mutation_audit_execution, "clear_mutmut_worktree") as clear:
                    with patch.object(mutation_audit_execution, "run_mutmut_for_target", return_value=run):
                        with patch.object(mutation_audit_support, "generated_no_mutants", return_value=False):
                            result = mutation_audit_execution.run_target(
                                repo=repo,
                                out_dir=out_dir,
                                mutmut=["mutmut"],
                                target=target,
                                options=options,
                            )

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.counts["killed"], 1)
            clear.assert_called_once_with(repo, reuse_cache=False)

    def test_mutmut_command_prefers_local_python_then_executable_then_current_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            local_python = repo / ".venv-mutmut" / "bin" / "python"
            local_python.parent.mkdir(parents=True)
            local_python.write_text("", encoding="utf-8")
            self.assertEqual(mutation_audit_cli.mutmut_command(repo), [str(local_python), "-m", "mutmut"])

            local_python.unlink()
            with patch("shutil.which", return_value="/usr/bin/mutmut") as which:
                self.assertEqual(mutation_audit_cli.mutmut_command(repo), ["/usr/bin/mutmut"])
            which.assert_called_once_with("mutmut")

            with patch("shutil.which", return_value=None) as which:
                with patch.object(mutation_audit_cli, "current_python_has_mutmut", return_value=True) as has_mutmut:
                    self.assertEqual(mutation_audit_cli.mutmut_command(repo), [sys.executable, "-m", "mutmut"])
            which.assert_called_once_with("mutmut")
            has_mutmut.assert_called_once()

            with patch("shutil.which", return_value=None) as which:
                with patch.object(mutation_audit_cli, "current_python_has_mutmut", return_value=False) as has_mutmut:
                    self.assertIsNone(mutation_audit_cli.mutmut_command(repo))
            which.assert_called_once_with("mutmut")
            has_mutmut.assert_called_once()

    def test_current_python_has_mutmut_uses_subprocess_probe(self) -> None:
        with patch("subprocess.run", return_value=subprocess.CompletedProcess(["python"], 0)) as run:
            self.assertTrue(mutation_audit_cli.current_python_has_mutmut())
        run.assert_called_once_with(
            [sys.executable, "-c", "import mutmut"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with patch("subprocess.run", return_value=subprocess.CompletedProcess(["python"], 1)):
            self.assertFalse(mutation_audit_cli.current_python_has_mutmut())

    def test_pyproject_mutmut_config_round_trip_and_strip(self) -> None:
        original = "\n[tool.other]\nvalue = 1\n\n[tool.mutmut]\nold = true\n\n[tool.next]\nvalue = 2\n\n"
        stripped = mutation_audit_config.strip_tool_mutmut_section(original)
        updated = mutation_audit_config.pyproject_with_mutmut_config(original, "scripts/dev/run_mutation_audit.py")
        parsed = tomllib.loads(updated)

        self.assertEqual(stripped, "[tool.other]\nvalue = 1\n\n[tool.next]\nvalue = 2\n")
        self.assertIn("[tool.other]", updated)
        self.assertIn("[tool.next]", updated)

    def test_mutmut_context_restores_only_its_owned_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            pyproject = repo / "pyproject.toml"
            pyproject.write_text(
                "[tool.before]\nvalue = 1\n\n[tool.mutmut]\nold = true\n\n[tool.after]\nvalue = 2\n",
                encoding="utf-8",
            )

            with mutation_audit_config.mutmut_config_for_target(repo, "venus_evcharger/target.py"):
                temporary = pyproject.read_text(encoding="utf-8")
                self.assertIn('only_mutate = ["venus_evcharger/target.py"]', temporary)
                pyproject.write_text(temporary.replace("value = 2", "value = 3"), encoding="utf-8")

            restored = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            self.assertEqual(restored["tool"]["before"]["value"], 1)
            self.assertEqual(restored["tool"]["after"]["value"], 3)
            self.assertTrue(restored["tool"]["mutmut"]["old"])

    def test_mutmut_section_restore_removes_temporary_section_when_original_had_none(self) -> None:
        current = "[tool.other]\nvalue = 2\n\n[tool.mutmut]\ntemporary = true\n"
        original = "[tool.other]\nvalue = 1\n"

        restored = mutation_audit_config.restore_tool_mutmut_section(current, original)

        self.assertEqual(tomllib.loads(restored), {"tool": {"other": {"value": 2}}})

    def test_atomic_mutmut_config_write_preserves_original_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pyproject.toml"
            path.write_text("original\n", encoding="utf-8")

            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    mutation_audit_config._write_text_atomically(path, "replacement\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "original\n")
            self.assertFalse((path.parent / ".pyproject.toml.mutation-audit.tmp").exists())


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _MemoryTextWriter:
    def __init__(self) -> None:
        self.text = ""
        self.flush_count = 0

    def __enter__(self) -> "_MemoryTextWriter":
        self.text = ""
        self.flush_count = 0
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def write(self, value: str) -> int:
        self.text += value
        return len(value)

    def flush(self) -> None:
        self.flush_count += 1


def _strict_write_open(writers: dict[Path, _MemoryTextWriter]):
    def open_path(path: Path, mode: str = "r", *args: object, **kwargs: object) -> _MemoryTextWriter:
        if args:
            raise AssertionError(f"unexpected positional open args: {args!r}")
        if mode != "w":
            raise AssertionError(f"unexpected open mode: {mode!r}")
        if kwargs != {"encoding": "utf-8"}:
            raise AssertionError(f"unexpected open kwargs: {kwargs!r}")
        try:
            return writers[path]
        except KeyError as error:
            raise AssertionError(f"unexpected opened path: {path}") from error

    return open_path


def _strict_append_open(writers: dict[Path, _MemoryTextWriter]):
    def open_path(path: Path, mode: str = "r", *args: object, **kwargs: object) -> _MemoryTextWriter:
        if args:
            raise AssertionError(f"unexpected positional open args: {args!r}")
        if mode != "a":
            raise AssertionError(f"unexpected open mode: {mode!r}")
        if kwargs != {"encoding": "utf-8"}:
            raise AssertionError(f"unexpected open kwargs: {kwargs!r}")
        try:
            return writers[path]
        except KeyError as error:
            raise AssertionError(f"unexpected opened path: {path}") from error

    return open_path


if __name__ == "__main__":
    unittest.main()
