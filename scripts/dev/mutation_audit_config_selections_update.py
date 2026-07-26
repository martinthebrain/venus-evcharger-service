# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutmut test selections for update-cycle and ordered fallback mutation selections."""

from __future__ import annotations


_UPDATE_RUNTIME_TESTS = (
    "tests/test_venus_evcharger_update_cycle_controller.py",
    "tests/test_update_edge_contracts.py",
)
_UPDATE_TIME_SAFETY_TESTS = ("tests/test_update_time_safety_contracts.py",)
_UPDATE_INPUT_VALIDATION_TESTS = ("tests/test_update_input_validation_contracts.py",)
_PHASE_SWITCH_PERSISTENCE_TESTS = ("tests/test_phase_switch_persistence_contracts.py",)


FOCUSED_TEST_SELECTIONS_UPDATE: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "venus_evcharger/update/offline_publish.py",
        (*_UPDATE_RUNTIME_TESTS, "tests/test_venus_evcharger_branch_misc.py", *_UPDATE_INPUT_VALIDATION_TESTS),
    ),
    (
        "venus_evcharger/update/pm_snapshot.py",
        (*_UPDATE_RUNTIME_TESTS, "tests/test_remaining_coverage_helpers.py", *_UPDATE_TIME_SAFETY_TESTS),
    ),
    ("venus_evcharger/update/readback_resolver.py", _UPDATE_RUNTIME_TESTS),
    ("venus_evcharger/update/relay_charger_current.py", _UPDATE_RUNTIME_TESTS),
    ("venus_evcharger/update/relay_charger_current_targets.py", (*_UPDATE_RUNTIME_TESTS, *_UPDATE_TIME_SAFETY_TESTS)),
    ("venus_evcharger/update/relay_charger_health.py", _UPDATE_RUNTIME_TESTS),
    ("venus_evcharger/update/controller.py", ("tests/test_update_controller_transport_contracts.py",)),
    (
        "venus_evcharger/update/relay_charger_transport.py",
        ("tests/test_update_controller_transport_contracts.py", *_UPDATE_RUNTIME_TESTS),
    ),
    ("venus_evcharger/update/relay_phase_decision.py", (*_UPDATE_RUNTIME_TESTS, *_UPDATE_INPUT_VALIDATION_TESTS)),
    ("venus_evcharger/update/relay_phase_publish.py", _UPDATE_RUNTIME_TESTS),
    ("venus_evcharger/update/relay_phase_switch_policy.py", (*_UPDATE_RUNTIME_TESTS, *_PHASE_SWITCH_PERSISTENCE_TESTS)),
    ("venus_evcharger/update/relay_status_publish.py", _UPDATE_RUNTIME_TESTS),
    (
        "venus_evcharger/update/state.py",
        (*_UPDATE_RUNTIME_TESTS, "tests/test_backend_readback_snapshot_contracts.py", *_UPDATE_TIME_SAFETY_TESTS),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_apply_pid.py",
        ("tests/test_victron_ess_balance_apply_pid_contracts.py",),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_apply_sources.py",
        ("tests/test_victron_ess_balance_apply_sources_contracts.py",),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_apply_write.py",
        ("tests/test_victron_ess_balance_apply_write_contracts.py",),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_recommendation_support.py",
        ("tests/test_victron_ess_balance_recommendation_support_contracts.py",),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_recommendation.py",
        ("tests/test_victron_ess_balance_recommendation_contracts.py",),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_learning_profiles_support.py",
        (
            "tests/test_victron_ess_balance_learning_profiles_support_contracts.py",
            "tests/test_branch_coverage_hotspots.py",
        ),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_learning_profiles.py",
        (
            "tests/test_victron_ess_balance_learning_profiles_contracts.py",
            "tests/test_victron_ess_balance_learning_profiles_support_contracts.py",
        ),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_learning_telemetry.py",
        (
            "tests/test_victron_ess_balance_learning_telemetry.py",
            "tests/test_victron_ess_balance_learning_telemetry_contracts.py",
            "tests/test_victron_ess_balance_learning_profiles_contracts.py",
        ),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_adaptive.py",
        (
            "tests/test_victron_ess_balance_adaptive_contracts.py",
            "tests/test_venus_evcharger_update_cycle_controller.py",
            "tests/test_branch_coverage_hotspots.py",
        ),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_apply.py",
        (
            "tests/test_victron_ess_balance_apply_contracts.py",
            "tests/test_victron_ess_balance_apply_restore_contracts.py",
            "tests/test_venus_evcharger_update_cycle_controller.py",
            "tests/test_branch_coverage_hotspots.py",
        ),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_safety_support.py",
        (
            "tests/test_victron_ess_balance_safety_contracts.py",
            "tests/test_victron_ess_balance_safety_threshold_contracts.py",
            "tests/test_victron_ess_balance_safety_support_contracts.py",
            "tests/test_branch_coverage_hotspots.py",
            "tests/test_victron_ess_balance_learning_telemetry.py",
        ),
    ),
    (
        "venus_evcharger/update/victron_ess_balance_safety.py",
        (
            "tests/test_victron_ess_balance_safety_contracts.py",
            "tests/test_victron_ess_balance_safety_threshold_contracts.py",
            "tests/test_victron_ess_balance_safety_support_contracts.py",
            "tests/test_branch_coverage_hotspots.py",
            "tests/test_victron_ess_balance_learning_telemetry.py",
        ),
    ),
    (
        "venus_evcharger/update/",
        (
            "tests/test_venus_evcharger_update_cycle_controller.py",
            "tests/test_branch_coverage_hotspots.py",
            "tests/test_victron_ess_balance_learning_telemetry.py",
        ),
    ),
    ("venus_evcharger/controllers/state_", ("tests/test_venus_evcharger_state_controller.py",)),
    ("venus_evcharger/topology/", ("tests/test_topology_config.py",)),
)
