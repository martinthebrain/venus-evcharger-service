# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused mutmut test selections for device backend mutation selections."""

from __future__ import annotations


FOCUSED_TEST_SELECTIONS_BACKEND: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "venus_evcharger/backend/template_meter.py",
        (
            "tests/test_venus_evcharger_backend_template_meter.py",
            "tests/test_venus_evcharger_backend_template_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_switch.py",
        (
            "tests/test_venus_evcharger_backend_template_switch.py",
            "tests/test_venus_evcharger_backend_template_support.py",
            "tests/test_venus_evcharger_backend_tuya.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_charger.py",
        (
            "tests/test_venus_evcharger_backend_template_charger.py",
            "tests/test_venus_evcharger_backend_template_support.py",
        ),
    ),
    ("venus_evcharger/backend/http_json_transport.py", ("tests/test_external_energy_io_boundaries.py",)),
    ("venus_evcharger/backend/template_http_transport.py", ("tests/test_venus_evcharger_backend_template_support.py",)),
    (
        "venus_evcharger/backend/template_support.py",
        (
            "tests/test_venus_evcharger_backend_template_support.py",
            "tests/test_venus_evcharger_backend_template_meter.py",
            "tests/test_venus_evcharger_backend_template_switch.py",
            "tests/test_venus_evcharger_backend_template_charger.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_support.py",
        (
            "tests/test_venus_evcharger_backend_shelly_support.py",
            "tests/test_venus_evcharger_backend_shelly_meter.py",
            "tests/test_venus_evcharger_backend_switch.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_io_runtime.py",
        (
            "tests/test_venus_evcharger_shelly_io_controller.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
            "tests/test_venus_evcharger_backend_shelly_meter.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_meter.py",
        ("tests/test_venus_evcharger_backend_shelly_meter.py", "tests/test_venus_evcharger_backend_shelly_support.py"),
    ),
    (
        "venus_evcharger/backend/shelly_switch.py",
        ("tests/test_venus_evcharger_backend_switch.py", "tests/test_venus_evcharger_backend_shelly_support.py"),
    ),
    ("venus_evcharger/backend/base.py", ("tests/test_backend_base_contracts.py",)),
    (
        "venus_evcharger/backend/cerbo_gx_relay_switch.py",
        ("tests/test_venus_evcharger_backend_cerbo_gx_relay_switch.py",),
    ),
    (
        "venus_evcharger/backend/goe_charger.py",
        (
            "tests/test_venus_evcharger_backend_goe_charger.py",
            "tests/venus_evcharger_backend_factory_charger_cases.py",
            "tests/venus_evcharger_backend_probe_command_cases.py",
        ),
    ),
    (
        "venus_evcharger/backend/modbus_charger.py",
        (
            "tests/test_venus_evcharger_backend_modbus_charger.py",
            "tests/test_venus_evcharger_backend_modbus_profiles.py",
            "tests/venus_evcharger_backend_factory_charger_cases.py",
        ),
    ),
    ("venus_evcharger/backend/modbus_client.py", ("tests/test_venus_evcharger_backend_modbus_client.py",)),
    (
        "venus_evcharger/backend/modbus_profile_models.py",
        (
            "tests/test_venus_evcharger_backend_modbus_profiles.py",
            "tests/test_venus_evcharger_backend_modbus_charger.py",
        ),
    ),
    (
        "venus_evcharger/backend/modbus_profiles.py",
        (
            "tests/test_venus_evcharger_backend_modbus_profiles.py",
            "tests/test_venus_evcharger_backend_modbus_charger.py",
            "tests/venus_evcharger_backend_factory_charger_cases.py",
        ),
    ),
    ("venus_evcharger/backend/modbus_transport.py", ("tests/test_venus_evcharger_backend_modbus_transport.py",)),
    ("venus_evcharger/backend/modbus_transport_config.py", ("tests/test_venus_evcharger_backend_modbus_transport.py",)),
    ("venus_evcharger/backend/models.py", ("tests/test_venus_evcharger_backend_models.py",)),
    ("venus_evcharger/backend/native_modbus_backend.py", ("tests/test_native_modbus_backend_contracts.py",)),
    ("venus_evcharger/backend/shelly_contactor_switch.py", ("tests/test_shelly_contactor_switch_contracts.py",)),
    ("venus_evcharger/backend/shelly_io.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_io_capabilities.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_io_ports.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_io_requests.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_io_runtime_cache.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_io_split.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_io_types.py", ("tests/test_venus_evcharger_backend_shelly_support.py",)),
    (
        "venus_evcharger/backend/shelly_io_worker.py",
        ("tests/test_venus_evcharger_shelly_io_controller.py", "tests/test_venus_evcharger_backend_shelly_support.py"),
    ),
    ("venus_evcharger/backend/shelly_io_worker_lifecycle.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_io_worker_transport.py", ("tests/test_venus_evcharger_shelly_io_controller.py",)),
    ("venus_evcharger/backend/shelly_profiles.py", ("tests/test_venus_evcharger_backend_shelly_support.py",)),
    ("venus_evcharger/backend/shelly_support_phase.py", ("tests/test_venus_evcharger_backend_shelly_support.py",)),
    ("venus_evcharger/backend/simpleevse_charger.py", ("tests/test_venus_evcharger_backend_simpleevse_charger.py",)),
    ("venus_evcharger/backend/smartevse_charger.py", ("tests/test_venus_evcharger_backend_smartevse_charger.py",)),
    ("venus_evcharger/backend/switch_group.py", ("tests/test_venus_evcharger_backend_switch.py",)),
    ("venus_evcharger/backend/tasmota_meter.py", ("tests/test_venus_evcharger_backend_tasmota.py",)),
    ("venus_evcharger/backend/tasmota_switch.py", ("tests/test_venus_evcharger_backend_tasmota.py",)),
    (
        "venus_evcharger/backend/template_charger_contract.py",
        ("tests/test_venus_evcharger_backend_template_charger.py",),
    ),
)
