# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the compact semantic energy-input transport."""

from __future__ import annotations

import struct
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

import venus_evcharger.ipc.energy_binary as binary
import venus_evcharger.dbus_gateway_cache as gateway_cache
import venus_evcharger.dbus_gateway_cache_io as gateway_cache_io
from venus_evcharger.dbus_gateway_cache import DbusCacheStore
from venus_evcharger.dbus_gateway_client import GatewayClient
from venus_evcharger.dbus_gateway_core import gateway_paths, write_json_file
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergySourceDescriptor,
    EnergyTopologySnapshot,
    MeasuredValue,
)
from venus_evcharger.ipc.energy_types import EnergyValueStatus


def _measurement(
    value: float | None,
    *,
    status: EnergyValueStatus = "fresh",
    source_ids: tuple[str, ...] = ("source-a",),
    reason_code: str = "",
) -> MeasuredValue:
    return MeasuredValue(
        value=value,
        observed_at=100.0 if status in {"fresh", "stale"} else 0.0,
        status=status,
        confidence=0.75,
        source_ids=source_ids,
        reason_code=reason_code,
        observed_monotonic=100.0 if status in {"fresh", "stale"} else 0.0,
    )


def _snapshot() -> EnergyInputsSnapshot:
    return EnergyInputsSnapshot(
        sequence=7,
        captured_at=101.0,
        captured_monotonic=101.0,
        topology_generation=3,
        grid_power_w=_measurement(-123.5, source_ids=("grid",)),
        pv_power_w=_measurement(0.0, status="stale", source_ids=("pv-ac", "pv-dc"), reason_code="night"),
        battery_soc=_measurement(None, status="unavailable", source_ids=(), reason_code="missing"),
        battery_net_power_w=_measurement(-250.0, source_ids=("battery",)),
        battery_capacity_wh=_measurement(5_120.0, source_ids=("battery",)),
        battery_capacity_ah=_measurement(100.0, source_ids=("battery",)),
        battery_voltage_v=_measurement(52.8, source_ids=("battery",)),
    )


class EnergyBinaryIpcContractTests(unittest.TestCase):
    def test_monotonic_freshness_reference_accepts_zero_only_at_the_boundary(self) -> None:
        self.assertTrue(binary._valid_current_monotonic(0.0))
        self.assertTrue(binary._valid_current_monotonic(0.5))
        self.assertFalse(binary._valid_current_monotonic(-0.5))
        self.assertFalse(binary._valid_current_monotonic(float("-inf")))
        self.assertFalse(binary._valid_current_monotonic(float("nan")))

    def test_round_trip_preserves_typed_snapshot_without_json(self) -> None:
        snapshot = _snapshot()
        encoded = binary.encode_energy_inputs(snapshot)

        self.assertEqual(encoded[:4], b"VEI4")
        self.assertEqual(binary.decode_energy_inputs(encoded), snapshot)
        self.assertLess(len(encoded), len(str(snapshot.to_payload()).encode()))

    def test_wire_format_and_exact_payload_limit_are_canonical(self) -> None:
        snapshot = EnergyInputsSnapshot(
            sequence=1,
            captured_at=7.0,
            captured_monotonic=8.0,
            topology_generation=3,
            grid_power_w=MeasuredValue(4.0, 5.0, "fresh", 0.5, (), "", observed_monotonic=6.0),
            pv_power_w=MeasuredValue(None, 0.0, "unavailable", 0.0, (), "", observed_monotonic=0.0),
            battery_soc=MeasuredValue(6.0, 7.0, "error", 0.25, (), "", observed_monotonic=7.0),
            battery_net_power_w=MeasuredValue(-8.0, 7.0, "fresh", 0.75, (), "", observed_monotonic=8.0),
            battery_capacity_wh=MeasuredValue(9.0, 7.0, "fresh", 1.0, (), "", observed_monotonic=8.0),
            battery_capacity_ah=MeasuredValue(10.0, 7.0, "fresh", 1.0, (), "", observed_monotonic=8.0),
            battery_voltage_v=MeasuredValue(11.0, 7.0, "fresh", 1.0, (), "", observed_monotonic=8.0),
        )
        expected = b"".join(
            (
                struct.pack(">4sBQQdd", b"VEI4", 4, 1, 3, 7.0, 8.0),
                struct.pack(">BBddddH", 0, 1, 4.0, 5.0, 6.0, 0.5, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 2, 0, 0.0, 0.0, 0.0, 0.0, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 3, 1, 6.0, 7.0, 7.0, 0.25, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 0, 1, -8.0, 7.0, 8.0, 0.75, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 0, 1, 9.0, 7.0, 8.0, 1.0, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 0, 1, 10.0, 7.0, 8.0, 1.0, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 0, 1, 11.0, 7.0, 8.0, 1.0, 0),
                b"\x00\x00",
            )
        )

        with patch.object(binary, "_MAX_PAYLOAD_BYTES", len(expected)):
            self.assertEqual(binary.encode_energy_inputs(snapshot), expected)
            self.assertEqual(binary.decode_energy_inputs(expected), snapshot)

    def test_vei3_payload_remains_readable_with_explicit_missing_capacity(self) -> None:
        legacy = b"".join(
            (
                struct.pack(">4sBQQdd", b"VEI3", 3, 1, 3, 7.0, 8.0),
                struct.pack(">BBddddH", 0, 1, 4.0, 5.0, 6.0, 0.5, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 2, 0, 0.0, 0.0, 0.0, 0.0, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 3, 1, 6.0, 7.0, 7.0, 0.25, 0),
                b"\x00\x00",
                struct.pack(">BBddddH", 0, 1, -8.0, 7.0, 8.0, 0.75, 0),
                b"\x00\x00",
            )
        )

        decoded = binary.decode_energy_inputs(legacy)

        self.assertEqual(decoded.schema_version, 4)
        self.assertEqual(decoded.grid_power_w.value, 4.0)
        for measurement in (
            decoded.battery_capacity_wh,
            decoded.battery_capacity_ah,
            decoded.battery_voltage_v,
        ):
            self.assertIsNone(measurement.value)
            self.assertEqual(measurement.status, "unknown")
            self.assertEqual(measurement.reason_code, "not-observed")

    def test_atomic_file_load_enforces_age_and_supports_unbounded_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "energy.bin")
            binary.write_energy_inputs_file(path, _snapshot())

            self.assertEqual(binary.load_energy_inputs_file(path, max_age_seconds=2.0, now_monotonic=102.999), _snapshot())
            self.assertEqual(binary.load_energy_inputs_file(path, max_age_seconds=2.0, now_monotonic=103.0), _snapshot())
            self.assertIsNone(binary.load_energy_inputs_file(path, max_age_seconds=2.0, now_monotonic=103.001))
            self.assertEqual(binary.load_energy_inputs_file(path, max_age_seconds=0.0, now_monotonic=101.0), _snapshot())
            self.assertIsNone(binary.load_energy_inputs_file(path, max_age_seconds=0.0, now_monotonic=101.001))
            self.assertIsNone(binary.load_energy_inputs_file(path, max_age_seconds=2.0, now_monotonic=100.0))
            self.assertEqual(binary.load_energy_inputs_file(path, max_age_seconds=-1.0, now_monotonic=999.0), _snapshot())
            self.assertIsNone(binary.load_energy_inputs_file(path + ".missing", max_age_seconds=2.0))

    def test_file_loader_bounds_input_before_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "oversized.bin"
            path.write_bytes(b"x" * (binary._MAX_PAYLOAD_BYTES + 2))
            with patch.object(
                binary,
                "decode_energy_inputs",
                side_effect=AssertionError("oversized payload must not be decoded"),
            ):
                self.assertIsNone(
                    binary.load_energy_inputs_file(
                        str(path),
                        max_age_seconds=1.0,
                        now_monotonic=1.0,
                    )
                )

    def test_bounded_reader_requests_one_guard_byte_and_reports_exact_limit(self) -> None:
        path = Path("/run/test-energy-inputs.bin")
        with patch.object(Path, "open") as open_file:
            handle = open_file.return_value.__enter__.return_value
            handle.read.return_value = b"abc"
            with patch.object(binary, "_MAX_PAYLOAD_BYTES", 3):
                self.assertEqual(binary._read_bounded_payload(path), b"abc")
            open_file.assert_called_once_with("rb")
            handle.read.assert_called_once_with(4)

        with tempfile.TemporaryDirectory() as temp_dir:
            oversized = Path(temp_dir) / "oversized.bin"
            oversized.write_bytes(b"abcd")
            with (
                patch.object(binary, "_MAX_PAYLOAD_BYTES", 3),
                self.assertRaises(ValueError) as raised,
            ):
                binary._read_bounded_payload(oversized)
            self.assertEqual(str(raised.exception), binary._PAYLOAD_SIZE_ERROR)

    def test_decoder_rejects_corrupt_envelopes(self) -> None:
        encoded = binary.encode_energy_inputs(_snapshot())
        cases = (
            b"",
            b"bad!" + encoded[4:],
            encoded[:4] + b"\x05" + encoded[5:],
            encoded[:-1],
            encoded + b"x",
            encoded[: binary._HEADER.size] + b"\xff" + encoded[binary._HEADER.size + 1 :],
            encoded[: binary._HEADER.size + 1] + b"\x02" + encoded[binary._HEADER.size + 2 :],
            b"x" * 65537,
        )
        for payload in cases:
            with self.subTest(size=len(payload), prefix=payload[:6]):
                with self.assertRaises(ValueError):
                    binary.decode_energy_inputs(payload)

    def test_decoder_reports_stable_corruption_reasons(self) -> None:
        encoded = binary.encode_energy_inputs(_snapshot())
        cases = (
            (b"x" * 65537, binary._PAYLOAD_SIZE_ERROR),
            (b"bad!" + encoded[4:], binary._INVALID_MAGIC_ERROR),
            (encoded[:4] + b"\x05" + encoded[5:], binary._UNSUPPORTED_SCHEMA_ERROR),
            (
                encoded[: binary._HEADER.size] + b"\xff" + encoded[binary._HEADER.size + 1 :],
                binary._INVALID_STATUS_ERROR,
            ),
            (
                encoded[: binary._HEADER.size + 1] + b"\x02" + encoded[binary._HEADER.size + 2 :],
                binary._INVALID_VALUE_MARKER_ERROR,
            ),
            (b"", binary._TRUNCATED_PAYLOAD_ERROR),
            (encoded[:-2] + b"\x00\x01", binary._TRUNCATED_TEXT_ERROR),
            (encoded + b"x", binary._TRAILING_DATA_ERROR),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError) as raised:
                    binary.decode_energy_inputs(payload)
                self.assertEqual(str(raised.exception), reason)

    def test_reader_validates_struct_wire_types_without_static_casts(self) -> None:
        reader = binary._BinaryReader(b"")
        with patch.object(
            reader,
            "unpack",
            return_value=("VEI3", 3, 1, 1, 1.0, 1.0),
        ):
            with self.assertRaisesRegex(ValueError, "invalid wire value type"):
                reader.header()
        with patch.object(
            reader,
            "unpack",
            return_value=(0, 1, 2, 3.0, 4.0, 5.0, 0),
        ):
            with self.assertRaisesRegex(ValueError, "invalid wire value type"):
                reader.measurement_fields()
        with patch.object(reader, "unpack", return_value=(1.0,)):
            with self.assertRaisesRegex(ValueError, "invalid wire value type"):
                reader.text()

    def test_decoder_rejects_invalid_utf8_and_source_count(self) -> None:
        encoded = bytearray(binary.encode_energy_inputs(_snapshot()))
        first_text_length_offset = binary._HEADER.size + binary._MEASUREMENT.size
        encoded[first_text_length_offset : first_text_length_offset + 2] = struct.pack(">H", 1)
        encoded[first_text_length_offset + 2] = 0xFF
        with self.assertRaisesRegex(ValueError, "not UTF-8"):
            binary.decode_energy_inputs(bytes(encoded))

        too_many_sources = bytearray(binary.encode_energy_inputs(_snapshot()))
        source_count_offset = binary._HEADER.size + binary._MEASUREMENT.size - 2
        too_many_sources[source_count_offset : source_count_offset + 2] = struct.pack(">H", 65)
        with self.assertRaisesRegex(ValueError, "too many source_ids"):
            binary.decode_energy_inputs(bytes(too_many_sources))

    def test_encoder_enforces_wire_ranges_and_size_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the binary wire range"):
            binary.encode_energy_inputs(replace(_snapshot(), sequence=2**65))
        maximum_sources = tuple(f"s{index}" for index in range(64))
        exact_source_limit = replace(
            _snapshot(),
            grid_power_w=_measurement(1.0, source_ids=maximum_sources),
        )
        self.assertEqual(
            binary.decode_energy_inputs(binary.encode_energy_inputs(exact_source_limit)),
            exact_source_limit,
        )
        with self.assertRaisesRegex(ValueError, "too many source_ids"):
            binary.encode_energy_inputs(
                replace(_snapshot(), grid_power_w=_measurement(1.0, source_ids=tuple(f"s{index}" for index in range(65))))
            )
        maximum_text = binary._encode_text("x" * 65535)
        self.assertEqual(maximum_text[:2], b"\xff\xff")
        self.assertEqual(len(maximum_text), 65537)
        with self.assertRaisesRegex(ValueError, "text field exceeds"):
            binary.encode_energy_inputs(
                replace(_snapshot(), grid_power_w=_measurement(1.0, reason_code="x" * 65536))
            )
        large_sources = tuple("x" * 2000 + str(index) for index in range(40))
        with self.assertRaisesRegex(ValueError, "payload exceeds"):
            binary.encode_energy_inputs(
                replace(_snapshot(), grid_power_w=_measurement(1.0, source_ids=large_sources))
            )

    def test_snapshot_orders_measurements_only_in_the_monotonic_domain(self) -> None:
        boundary = replace(
            _snapshot(),
            grid_power_w=MeasuredValue(
                1.0,
                1000.0,
                "fresh",
                1.0,
                observed_monotonic=101.0,
            ),
        )
        self.assertEqual(
            binary.decode_energy_inputs(binary.encode_energy_inputs(boundary)),
            boundary,
        )
        with self.assertRaisesRegex(
            ValueError,
            "grid_power_w observed_monotonic exceeds captured_monotonic",
        ):
            replace(
                _snapshot(),
                grid_power_w=MeasuredValue(
                    1.0,
                    1.0,
                    "fresh",
                    1.0,
                    observed_monotonic=101.001,
                ),
            )

    def test_loader_treats_invalid_files_and_clock_conversion_errors_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "energy.bin"
            path.write_bytes(b"invalid")
            self.assertIsNone(binary.load_energy_inputs_file(str(path), max_age_seconds=2.0))
            path.write_bytes(binary.encode_energy_inputs(_snapshot()))
            for current, maximum in (
                (cast(float, "bad"), 2.0),
                (float("nan"), 2.0),
                (float("inf"), 2.0),
                (-1.0, 2.0),
                (101.0, float("nan")),
                (101.0, float("inf")),
            ):
                with self.subTest(current=current, maximum=maximum):
                    self.assertIsNone(
                        binary.load_energy_inputs_file(
                            str(path),
                            max_age_seconds=maximum,
                            now_monotonic=current,
                        )
                    )

    def test_binary_writer_propagates_atomic_write_failures(self) -> None:
        with patch.object(binary, "write_bytes_atomically", side_effect=OSError("full")):
            with self.assertRaisesRegex(OSError, "full"):
                binary.write_energy_inputs_file("/tmp/energy.bin", _snapshot())

    def test_gateway_client_prefers_split_snapshots_and_falls_back_to_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            self.assertEqual(paths.energy_inputs_path, str(Path(paths.run_dir) / "energy-inputs.v4.bin"))
            self.assertEqual(paths.energy_topology_path, str(Path(paths.run_dir) / "energy-topology.json"))
            captured_at = time.time()
            inputs = replace(
                _snapshot(),
                captured_at=captured_at,
                captured_monotonic=time.monotonic(),
            )
            topology = EnergyTopologySnapshot(
                generation=inputs.topology_generation,
                captured_at=captured_at,
                sources=(
                    EnergySourceDescriptor("grid-main", "grid", "online", ("power",)),
                ),
            )
            store = DbusCacheStore(paths)
            store.set_semantic_energy_snapshots(inputs, topology)
            store.write_energy_inputs_snapshot()
            store.write_energy_topology_snapshot()
            store.write_cache_snapshot()
            store.write_health_snapshot()
            client = GatewayClient(paths)

            with patch.object(client, "load_cache", side_effect=AssertionError("full cache must not be parsed")):
                self.assertEqual(client.load_energy_inputs(), inputs)
                self.assertEqual(client.load_energy_topology(), topology)

            Path(paths.energy_inputs_path).unlink()
            Path(paths.energy_topology_path).unlink()
            self.assertEqual(client.load_energy_inputs(), inputs)
            self.assertIsNone(client.load_energy_topology())

            write_json_file(
                paths.energy_topology_path,
                {"captured_at": captured_at, "invalid": True},
            )
            self.assertIsNone(client.load_energy_topology())

            with (
                patch.object(
                    gateway_cache.DbusCacheStore,
                    "load_snapshot",
                    return_value=topology.to_payload(),
                ) as load_split,
                patch.object(client, "load_health", return_value={"state": "ok"}) as load_health,
            ):
                self.assertEqual(client.load_energy_topology(max_age_seconds=12.5), topology)
            load_split.assert_called_once_with(
                paths.energy_topology_path,
                max_age_seconds=-1.0,
            )
            load_health.assert_called_once_with(max_age_seconds=12.5)

    def test_cache_store_publishes_partial_semantic_snapshots_independently(self) -> None:
        inputs = _snapshot()
        topology = EnergyTopologySnapshot(
            generation=inputs.topology_generation,
            captured_at=inputs.captured_at,
            sources=(),
        )
        store = DbusCacheStore(gateway_paths("/run/test-energy-binary"))
        self.assertIsNone(store._energy_inputs_snapshot)
        self.assertIsNone(store._energy_topology_snapshot)

        with (
            patch.object(gateway_cache_io, "write_energy_inputs_file") as write_inputs,
            patch.object(gateway_cache_io, "write_json_file") as write_json,
        ):
            store._energy_inputs_snapshot = inputs
            store.write_energy_inputs_snapshot()
            store.write_energy_topology_snapshot()
            write_inputs.assert_called_once_with(store.paths.energy_inputs_path, inputs)
            write_json.assert_not_called()

            write_inputs.reset_mock()
            store._energy_inputs_snapshot = None
            store._energy_topology_snapshot = topology
            store.write_energy_inputs_snapshot()
            store.write_energy_topology_snapshot()
            write_inputs.assert_not_called()
            write_json.assert_called_once_with(store.paths.energy_topology_path, topology.to_payload())


if __name__ == "__main__":
    unittest.main()
