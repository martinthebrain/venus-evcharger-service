# SPDX-License-Identifier: GPL-3.0-or-later
"""Cooperative deadline enforcement across external energy connectors."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.energy import connectors
from venus_evcharger.energy.connectors_command import (
    CommandJsonEnergySourceSettings,
    _command_timeout_seconds,
)
from venus_evcharger.energy.connectors_common import (
    EnergySourceHttpClient,
    _bounded_request_timeout_seconds,
)
from venus_evcharger.energy.bounded_subprocess import BoundedCommandResult
from venus_evcharger.energy.connectors_modbus import (
    ModbusEnergyFieldSettings,
    ModbusEnergySourceSettings,
)
from venus_evcharger.energy.connectors_opendtu import _opendtu_timeout_seconds
from venus_evcharger.energy.connectors_template import _template_timeout_seconds
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot


class _Response:
    def __init__(self) -> None:
        self.headers = {"Content-Length": "12"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return {"ok": True}

    def iter_content(self, chunk_size: int) -> tuple[bytes, ...]:
        del chunk_size
        return (b'{"ok": true}',)

    def close(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def get(self, *, url: str, timeout: float, **_kwargs: object) -> _Response:
        del url
        self.timeout = timeout
        return _Response()


class _Runtime:
    def __init__(self, limit: float) -> None:
        self.limit = limit
        self.session = _Session()
        self.calls: list[float] = []
        self.shelly_request_timeout_seconds = 2.0

    def bounded_request_timeout_seconds(self, configured: float) -> float:
        self.calls.append(configured)
        return self.limit


def _command_settings() -> CommandJsonEnergySourceSettings:
    return CommandJsonEnergySourceSettings(
        command=("energy-helper",),
        timeout_seconds=5.0,
        soc_path="soc",
        usable_capacity_wh_path=None,
        battery_power_path=None,
        ac_power_path=None,
        pv_input_power_path=None,
        grid_interaction_path=None,
        operating_mode_path=None,
        online_path=None,
        confidence_path=None,
    )


def _transport() -> ModbusTransportSettings:
    return ModbusTransportSettings(
        transport_kind="tcp",
        unit_id=1,
        timeout_seconds=5.0,
        host="192.0.2.1",
        port=502,
        device=None,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        serial_port_owner="none",
        serial_port_owner_stop_command=None,
        serial_port_owner_start_command=None,
        serial_retry_count=0,
        serial_retry_delay_seconds=0.0,
    )


def _modbus_settings() -> ModbusEnergySourceSettings:
    return ModbusEnergySourceSettings(
        transport_settings=_transport(),
        soc_field=None,
        usable_capacity_field=None,
        battery_power_field=None,
        charge_limit_power_field=None,
        discharge_limit_power_field=None,
        ac_power_field=None,
        pv_input_power_field=None,
        grid_interaction_field=None,
        operating_mode_field=None,
        operating_mode_map={},
        ac_power_scope_key="",
        pv_input_power_scope_key="",
        grid_interaction_scope_key="",
    )


class EnergyConnectorDeadlineContracts(unittest.TestCase):
    def test_shared_timeout_boundary_and_http_request_use_runtime_limit(self) -> None:
        self.assertEqual(_bounded_request_timeout_seconds(object(), 2.0), 2.0)
        runtime = _Runtime(0.3)
        self.assertEqual(_bounded_request_timeout_seconds(runtime, 2.0), 0.3)
        runtime.limit = 4.0
        self.assertEqual(_bounded_request_timeout_seconds(runtime, 2.0), 2.0)
        runtime.limit = -1.0
        self.assertEqual(_bounded_request_timeout_seconds(runtime, 2.0), 0.001)

        runtime.limit = 0.25
        client = EnergySourceHttpClient(runtime, 5.0)
        self.assertEqual(client._perform_request("GET", "http://energy.local"), {"ok": True})
        self.assertEqual(runtime.session.timeout, 0.25)
        self.assertEqual(client.timeout_seconds, 5.0)

    def test_timeout_parsers_and_command_execution_honor_runtime_cap(self) -> None:
        runtime = _Runtime(0.4)
        self.assertEqual(_template_timeout_seconds(runtime, {"RequestTimeoutSeconds": "5"}), 0.4)
        self.assertEqual(_opendtu_timeout_seconds(runtime, {"RequestTimeoutSeconds": "5"}), 0.4)
        self.assertEqual(_command_timeout_seconds(runtime, {}, {"TimeoutSeconds": "5"}), 0.4)

        completed = BoundedCommandResult('{"soc": 62.0}', "")
        source = EnergySourceDefinition(source_id="command", role="battery")
        with (
            patch.object(
                connectors,
                "_command_json_energy_source_settings",
                return_value=_command_settings(),
            ),
            patch(
                "venus_evcharger.energy.connectors.run_bounded_command",
                return_value=completed,
            ) as run,
        ):
            step = connectors._command_json_energy_source_step(runtime, source, 10.0)

        snapshot = step.snapshot
        assert snapshot is not None
        self.assertEqual(snapshot.soc, 62.0)
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 0.4)
        self.assertEqual(run.call_args.kwargs["stdout_limit"], 262144)
        self.assertEqual(run.call_args.kwargs["stderr_limit"], 16384)

    def test_each_modbus_step_recomputes_remaining_deadline(self) -> None:
        runtime = _Runtime(0.2)
        client = MagicMock(spec=ModbusClient)
        client.timeout_seconds = 5.0
        source = EnergySourceDefinition(source_id="modbus", role="battery")
        settings = _modbus_settings()
        settings = settings.__class__(
            **{
                **settings.__dict__,
                "soc_field": ModbusEnergyFieldSettings(
                    "holding",
                    1,
                    "uint16",
                    1.0,
                    "big",
                ),
                "ac_power_field": ModbusEnergyFieldSettings(
                    "holding",
                    2,
                    "uint16",
                    1.0,
                    "big",
                ),
            }
        )
        client.read_scalar.side_effect = (62.0, 1200.0)

        with (
            patch.object(
                connectors,
                "_modbus_energy_source_settings",
                return_value=settings,
            ),
            patch.object(
                connectors,
                "_modbus_energy_source_client",
                return_value=client,
            ),
        ):
            first = connectors._modbus_energy_source_step(runtime, source, 10.0)
            second = connectors._modbus_energy_source_step(runtime, source, 10.0)

        self.assertFalse(first.complete)
        snapshot = second.snapshot
        assert snapshot is not None
        self.assertEqual((snapshot.soc, snapshot.ac_power_w), (62.0, 1200.0))
        self.assertEqual(client.timeout_seconds, 0.2)
        self.assertEqual(runtime.calls, [5.0, 5.0])


if __name__ == "__main__":
    unittest.main()
