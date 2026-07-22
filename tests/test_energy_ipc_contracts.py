# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import ast
import math
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import venus_evcharger.ipc.energy as energy_facade
from venus_evcharger.ipc.energy import (
    ENERGY_IPC_SCHEMA_VERSION,
    ENERGY_REFRESH_COMMAND_KIND,
    EnergySourceKind,
    EnergySourceState,
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergySourceDescriptor,
    EnergyTopologySnapshot,
    MeasuredValue,
)


def _measurement() -> MeasuredValue:
    return MeasuredValue(42.5, 100.0, "fresh", 0.9, ("source-a",), "")


class EnergyIpcContracts(unittest.TestCase):
    def test_public_facade_exports_only_the_stable_energy_contract(self) -> None:
        expected_exports = {
            "ENERGY_IPC_SCHEMA_VERSION",
            "ENERGY_REFRESH_COMMAND_KIND",
            "EnergyInputsSnapshot",
            "EnergyRefreshRequest",
            "EnergyRefreshScope",
            "EnergyRefreshUrgency",
            "EnergySourceDescriptor",
            "EnergySourceKind",
            "EnergySourceState",
            "EnergyTopologySnapshot",
            "EnergyValueStatus",
            "MeasuredValue",
        }
        self.assertEqual(set(energy_facade.__all__), expected_exports)
        self.assertTrue(all(hasattr(energy_facade, name) for name in expected_exports))

        facade_path = Path(energy_facade.__file__)
        facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in facade_tree.body))

    def test_runtime_consumers_import_energy_contracts_only_through_facade(self) -> None:
        package_root = Path(energy_facade.__file__).parents[1]
        implementation_modules = {
            "venus_evcharger.ipc.energy_refresh",
            "venus_evcharger.ipc.energy_snapshots",
            "venus_evcharger.ipc.energy_types",
            "venus_evcharger.ipc.energy_validation",
            "venus_evcharger.ipc.energy_values",
        }
        implementation_paths = {package_root / "ipc" / f"{module.rsplit('.', 1)[-1]}.py" for module in implementation_modules}
        implementation_paths.add(Path(energy_facade.__file__))
        violations: list[str] = []
        for path in package_root.rglob("*.py"):
            if path in implementation_paths:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in implementation_modules:
                    violations.append(f"{path.relative_to(package_root)}:{node.lineno}")
                if isinstance(node, ast.Import):
                    violations.extend(
                        f"{path.relative_to(package_root)}:{node.lineno}"
                        for alias in node.names
                        if alias.name in implementation_modules
                    )
        self.assertEqual(violations, [])

    def test_measurement_round_trip_preserves_quality(self) -> None:
        value = _measurement()
        self.assertEqual(MeasuredValue.from_payload(value.to_payload()), value)
        self.assertEqual(
            MeasuredValue.from_payload(
                {
                    "value": None,
                    "observed_at": 0,
                    "status": "unknown",
                    "confidence": 0,
                    "source_ids": [],
                    "reason_code": "not-observed",
                }
            ),
            MeasuredValue(None, 0.0, "unknown", 0.0, (), "not-observed"),
        )
        self.assertEqual(MeasuredValue(1.0, 1.0, "unavailable", 0.2).status, "unavailable")
        self.assertEqual(MeasuredValue(1.0, 1.0, "error", 0.2).status, "error")

    def test_measurement_rejects_invalid_direct_values(self) -> None:
        invalid: tuple[tuple[tuple[Any, ...], type[BaseException]], ...] = (
            ((True, 1.0, "fresh", 1.0), TypeError),
            ((math.inf, 1.0, "fresh", 1.0), ValueError),
            ((1.0, -1.0, "fresh", 1.0), ValueError),
            ((1.0, 1.0, "bogus", 1.0), ValueError),
            ((1.0, 1.0, "fresh", 1.1), ValueError),
            ((1.0, 1.0, "fresh", 1.0, ["source"]), TypeError),
            ((1.0, 1.0, "fresh", 1.0, ("source", "source")), ValueError),
            ((1.0, 1.0, "fresh", 1.0, (), 1), TypeError),
            ((None, 1.0, "fresh", 1.0), ValueError),
            ((1.0, 0.0, "stale", 1.0), ValueError),
        )
        for args, error in invalid:
            with self.subTest(args=args), self.assertRaises(error):
                MeasuredValue(*args)

    def test_descriptor_and_topology_round_trip(self) -> None:
        sources = (
            EnergySourceDescriptor("grid-primary", "grid", "online", ("power",)),
            EnergySourceDescriptor("pv-a", "pv_ac", "offline", ("power",)),
            EnergySourceDescriptor("pv-dc", "pv_dc", "unknown", ("power",)),
            EnergySourceDescriptor("battery-a", "battery", "online", ("soc",)),
        )
        topology = EnergyTopologySnapshot(3, 100.0, sources)
        self.assertEqual(EnergyTopologySnapshot.from_payload(topology.to_payload()), topology)

    def test_descriptor_and_topology_reject_invalid_shapes(self) -> None:
        with self.assertRaises(ValueError):
            EnergySourceDescriptor("source", cast(EnergySourceKind, "other"), "online", ())
        with self.assertRaises(ValueError):
            EnergySourceDescriptor("source", "grid", cast(EnergySourceState, "other"), ())
        with self.assertRaises(TypeError):
            EnergySourceDescriptor("source", "grid", "online", cast(tuple[str, ...], ["power"]))
        duplicate = EnergySourceDescriptor("same", "grid", "online", ("power",))
        with self.assertRaises(ValueError):
            EnergyTopologySnapshot(1, 1.0, (duplicate, duplicate))
        with self.assertRaises(TypeError):
            EnergyTopologySnapshot(1, 1.0, cast(tuple[EnergySourceDescriptor, ...], (object(),)))
        with self.assertRaises(ValueError):
            EnergyTopologySnapshot(-1, 1.0, ())
        with self.assertRaises(ValueError):
            EnergyTopologySnapshot(1, 0.0, ())
        with self.assertRaises(ValueError):
            EnergyTopologySnapshot(1, 1.0, (), schema_version=99)

    def test_inputs_round_trip_and_runtime_types(self) -> None:
        value = _measurement()
        inputs = EnergyInputsSnapshot(7, 101.0, 3, value, value, value)
        self.assertEqual(EnergyInputsSnapshot.from_payload(inputs.to_payload()), inputs)
        with self.assertRaises(TypeError):
            EnergyInputsSnapshot(1, 1.0, 1, cast(MeasuredValue, object()), value, value)
        with self.assertRaises(ValueError):
            EnergyInputsSnapshot(-1, 1.0, 1, value, value, value)
        with self.assertRaises(ValueError):
            EnergyInputsSnapshot(1, 1.0, -1, value, value, value)
        with self.assertRaises(ValueError):
            EnergyInputsSnapshot(1, 0.0, 1, value, value, value)
        with self.assertRaises(ValueError):
            EnergyInputsSnapshot(1, 1.0, 1, value, value, value, schema_version=2)

    def test_refresh_request_round_trip_and_transport_envelope(self) -> None:
        request = EnergyRefreshRequest("request-1", "pv", 5.0, "priority", reason="stale input")
        command = request.to_command(source="helper")
        self.assertEqual(command["kind"], ENERGY_REFRESH_COMMAND_KIND)
        self.assertEqual(command["schema_version"], ENERGY_IPC_SCHEMA_VERSION)
        self.assertEqual(command["priority"], "read")
        self.assertEqual(command["coalesce_key"], "energy-refresh:pv:all")
        command.update({"id": "transport-id", "created_at": 123.0, "queue_class": "read-fast"})
        self.assertEqual(EnergyRefreshRequest.from_command(command), request)

        normal = EnergyRefreshRequest("request-2", "energy_source", 0.0, source_id="source-a")
        normal_command = normal.to_command(source="helper")
        self.assertEqual(normal_command["priority"], "discovery")
        self.assertEqual(normal_command["coalesce_key"], "energy-refresh:energy_source:source-a")
        self.assertEqual(EnergyRefreshRequest.from_command(normal_command), normal)
        self.assertEqual(EnergyRefreshRequest("request-3", "battery", 1.0).scope, "battery")
        self.assertEqual(EnergyRefreshRequest("request-4", "topology", 1.0).scope, "topology")

    def test_refresh_request_rejects_invalid_semantics(self) -> None:
        invalid: tuple[tuple[tuple[Any, ...], type[BaseException]], ...] = (
            (("", "all", 1.0), ValueError),
            (("id", "invalid", 1.0), ValueError),
            (("id", "all", -1.0), ValueError),
            (("id", "all", 1.0, "invalid"), ValueError),
            (("id", "energy_source", 1.0), ValueError),
            (("id", "grid", 1.0, "normal", "source-a"), ValueError),
        )
        for args, error in invalid:
            with self.subTest(args=args), self.assertRaises(error):
                EnergyRefreshRequest(*args)
        with self.assertRaises(ValueError):
            EnergyRefreshRequest("id", "all", 1.0, schema_version=2)
        with self.assertRaises(TypeError):
            EnergyRefreshRequest("id", "all", 1.0, schema_version=True)
        with self.assertRaises(ValueError):
            EnergyRefreshRequest("id", "all", 1.0).to_command(source=" ")

    def test_refresh_parser_rejects_adapter_details_and_invalid_envelopes(self) -> None:
        valid = EnergyRefreshRequest("request", "all", 1.0).to_command(source="helper")
        invalid_payloads = (
            {**valid, "kind": "refresh_value"},
            {**valid, "schema_version": 99},
            {**valid, "scope": "energy_source", "source_id": None},
            {**valid, "scope": "grid", "source_id": "source-a"},
            {**valid, "service": "hidden"},
            {**valid, "path": "/hidden"},
            {**valid, "key": "hidden"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                EnergyRefreshRequest.from_command(payload)

    def test_snapshot_parsers_reject_untrusted_payload_shapes(self) -> None:
        measurement = _measurement().to_payload()
        inputs = EnergyInputsSnapshot(1, 1.0, 1, _measurement(), _measurement(), _measurement()).to_payload()
        topology = EnergyTopologySnapshot(1, 1.0, ()).to_payload()
        invalid_calls: tuple[tuple[Callable[[object], object], object], ...] = (
            (MeasuredValue.from_payload, []),
            (MeasuredValue.from_payload, {**measurement, "extra": 1}),
            (MeasuredValue.from_payload, {key: value for key, value in measurement.items() if key != "value"}),
            (MeasuredValue.from_payload, {**measurement, "source_ids": "bad"}),
            (MeasuredValue.from_payload, {1: "bad"}),
            (EnergyInputsSnapshot.from_payload, {**inputs, "schema_version": 99}),
            (EnergyInputsSnapshot.from_payload, {**inputs, "extra": 1}),
            (EnergyTopologySnapshot.from_payload, {**topology, "sources": "bad"}),
            (EnergyTopologySnapshot.from_payload, {**topology, "sources": ["bad"]}),
        )
        for parser, payload in invalid_calls:
            with self.subTest(parser=parser.__qualname__, payload=payload), self.assertRaises((TypeError, ValueError)):
                parser(payload)


if __name__ == "__main__":
    unittest.main()
