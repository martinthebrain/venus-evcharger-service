# SPDX-License-Identifier: GPL-3.0-or-later
import struct
import unittest
from unittest import mock

from venus_evcharger.backend.modbus_client import (
    ModbusClient,
    ModbusDeviceError,
    ModbusProtocolError,
    decode_register_value,
    encode_register_value,
    register_count,
)
from venus_evcharger.backend.modbus_transport import ModbusRequest


class _Transport:
    def __init__(self, response: bytes = b"") -> None:
        self.response = response
        self.requests: list[ModbusRequest] = []
        self.timeouts: list[float] = []

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        self.requests.append(request)
        self.timeouts.append(float(timeout_seconds))
        return self.response


class TestShellyWallboxBackendModbusClient(unittest.TestCase):
    def test_register_count_and_scalar_codec_cover_supported_types(self) -> None:
        self.assertEqual(register_count("uint32"), 2)
        self.assertEqual(register_count("int32"), 2)
        self.assertEqual(register_count("float32"), 2)
        self.assertEqual(register_count("uint16"), 1)

        self.assertTrue(decode_register_value((1,), "bool"))
        self.assertEqual(decode_register_value((12,), "uint16"), 12)
        self.assertEqual(decode_register_value((0xFFFE,), "int16"), -2)
        self.assertEqual(decode_register_value((0x1234, 0x5678), "uint32"), 0x12345678)
        self.assertEqual(decode_register_value((0xFFFF, 0xFFFE), "int32"), -2)
        self.assertAlmostEqual(
            float(decode_register_value((0x3FC0, 0x0000), "float32")),
            1.5,
        )
        self.assertEqual(
            decode_register_value((0x5678, 0x1234), "uint32", word_order="little"),
            0x12345678,
        )

        self.assertEqual(encode_register_value(True, "bool"), (1,))
        self.assertEqual(encode_register_value(12, "uint16"), (12,))
        self.assertEqual(encode_register_value(-2, "int16"), (0xFFFE,))
        self.assertEqual(encode_register_value(0x12345678, "uint32"), (0x1234, 0x5678))
        self.assertEqual(encode_register_value(-2, "int32"), (0xFFFF, 0xFFFE))
        encoded_float = encode_register_value(1.5, "float32")
        self.assertEqual(
            b"".join(part.to_bytes(2, "big") for part in encoded_float),
            struct.pack(">f", 1.5),
        )
        self.assertEqual(
            encode_register_value(0x12345678, "uint32", word_order="little"),
            (0x5678, 0x1234),
        )

        with self.assertRaises(ValueError) as empty_decode:
            decode_register_value((), "uint16")
        self.assertEqual(str(empty_decode.exception), "Modbus register decode requires at least one register")
        with self.assertRaises(ValueError) as unsupported_decode:
            decode_register_value((1,), "weird")
        self.assertEqual(str(unsupported_decode.exception), "Unsupported Modbus data type 'weird'")
        with self.assertRaises(ValueError) as unsupported_encode:
            encode_register_value(1, "weird")
        self.assertEqual(str(unsupported_encode.exception), "Unsupported Modbus data type 'weird'")

    def test_scalar_codec_edge_contracts(self) -> None:
        self.assertEqual(register_count(" UINT32 "), 2)
        self.assertEqual(register_count(" BOOL "), 1)
        self.assertFalse(decode_register_value((0,), "bool"))
        self.assertEqual(decode_register_value((0x8000,), "int16"), -32768)
        self.assertEqual(decode_register_value((0x7FFF,), "int16"), 32767)
        self.assertEqual(decode_register_value((0xFFFF, 0xFFFF), "uint32"), 0xFFFFFFFF)
        self.assertEqual(decode_register_value((0x8000, 0x0000), "int32"), -2147483648)
        self.assertEqual(
            decode_register_value((0x5678, 0x1234), "int32", word_order="little"),
            0x12345678,
        )
        self.assertEqual(encode_register_value(False, "bool"), (0,))
        self.assertEqual(encode_register_value(0xFFFFFFFF, "uint32"), (0xFFFF, 0xFFFF))
        self.assertEqual(encode_register_value(-2147483648, "int32"), (0x8000, 0x0000))
        little_float = encode_register_value(1.5, "float32", word_order="little")
        self.assertEqual(little_float, tuple(reversed(encode_register_value(1.5, "float32"))))

        with self.assertRaises(ValueError) as uppercase_decode_order:
            decode_register_value((0x1234, 0x5678), "uint32", word_order="BIG")
        self.assertEqual(str(uppercase_decode_order.exception), "Unsupported Modbus word order 'BIG'")
        with self.assertRaises(ValueError) as invalid_encode_order:
            encode_register_value(0x12345678, "uint32", word_order="middle")
        self.assertEqual(str(invalid_encode_order.exception), "Unsupported Modbus word order 'middle'")

        with mock.patch("venus_evcharger.backend.modbus_client.struct.pack", return_value=b"12345"):
            with self.assertRaises(ValueError) as malformed_float_payload:
                encode_register_value(1.5, "float32")
        self.assertEqual(
            str(malformed_float_payload.exception),
            "Modbus 32-bit payload requires exactly four bytes",
        )

    def test_response_pdu_sends_exact_request_and_timeout(self) -> None:
        transport = _Transport(bytes((0x03, 0x00)))
        client = ModbusClient(transport, unit_id="7", timeout_seconds="1.5")  # type: ignore[arg-type]

        payload = b"\x00\x10\x00\x02"
        response_data = client._response_pdu(0x03, payload)

        self.assertEqual(response_data, b"\x00")
        self.assertEqual(client.unit_id, 7)
        self.assertEqual(client.timeout_seconds, 1.5)
        self.assertEqual(transport.requests, [ModbusRequest(unit_id=7, function_code=0x03, payload=payload)])
        self.assertEqual(transport.timeouts, [1.5])

    def test_response_pdu_maps_empty_exception_and_wrong_function_frames(self) -> None:
        client = ModbusClient(_Transport(b""), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as empty_response:
            client._response_pdu(0x03, b"\x00\x00\x00\x01")
        self.assertEqual(str(empty_response.exception), "Empty Modbus response")

        client = ModbusClient(_Transport(bytes((0x83, 0x02))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusDeviceError, "0x02"):
            client._response_pdu(0x03, b"\x00\x00\x00\x01")

        client = ModbusClient(_Transport(bytes((0x90, 0x03))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusDeviceError, "0x03"):
            client._response_pdu(0x10, b"\x00\x00\x00\x01")

        client = ModbusClient(_Transport(bytes((0x83,))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusDeviceError, "0x-1"):
            client._response_pdu(0x03, b"\x00\x00\x00\x01")

        client = ModbusClient(_Transport(bytes((0x04, 0x00))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Unexpected Modbus response function"):
            client._response_pdu(0x03, b"\x00\x00\x00\x01")

    def test_read_bits_and_registers_validate_incomplete_frames(self) -> None:
        client = ModbusClient(_Transport(bytes((0x01, 0x02, 0b10101100, 0b00000010))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(
            client._read_bits("coil", 0x1234, 10),
            (False, False, True, True, False, True, False, True, False, True),
        )
        self.assertEqual(client.transport.requests[-1].payload, b"\x12\x34\x00\x0a")

        client = ModbusClient(_Transport(bytes((0x01, 0x02, 0x00, 0x01))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(client._read_bits("coil", 0, 9)[-1], True)

        client = ModbusClient(_Transport(bytes((0x01, 0x01, 0x01, 0x99))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as extra_bit_data:
            client._read_bits("coil", 0, 1)
        self.assertEqual(str(extra_bit_data.exception), "Incomplete Modbus bit response")

        client = ModbusClient(_Transport(bytes((0x01,))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as missing_bit_count:
            client._read_bits("coil", 0, 1)
        self.assertEqual(str(missing_bit_count.exception), "Missing Modbus bit byte-count")

        client = ModbusClient(_Transport(bytes((0x01, 0x02, 0x01))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Incomplete Modbus bit response"):
            client._read_bits("coil", 0, 9)

        client = ModbusClient(_Transport(bytes((0x03,))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as missing_register_count:
            client._read_registers("holding", 0, 1)
        self.assertEqual(str(missing_register_count.exception), "Missing Modbus register byte-count")

        client = ModbusClient(_Transport(bytes((0x03, 0x06, 0x12, 0x34, 0xAB, 0xCD, 0x00, 0x01))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(client._read_registers("holding", 0x0002, 3), (0x1234, 0xABCD, 0x0001))
        self.assertEqual(client.transport.requests[-1].payload, b"\x00\x02\x00\x03")

        client = ModbusClient(_Transport(bytes((0x03, 0x02, 0x12, 0x34, 0x99))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as extra_register_data:
            client._read_registers("holding", 0, 1)
        self.assertEqual(str(extra_register_data.exception), "Incomplete Modbus register response")

        client = ModbusClient(_Transport(bytes((0x03, 0x04, 0x00, 0x01))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Incomplete Modbus register response"):
            client._read_registers("holding", 0, 2)

    def test_read_scalar_and_write_methods_cover_success_and_length_failures(self) -> None:
        client = ModbusClient(_Transport(bytes((0x01, 0x01, 0x01))), unit_id=7, timeout_seconds=1.0)
        coil_bool = client.read_scalar("coil", 4, "bool")
        self.assertIs(coil_bool, True)
        request = client.transport.requests[-1]
        self.assertEqual(request.unit_id, 7)
        self.assertEqual(request.function_code, 0x01)
        self.assertEqual(request.payload, b"\x00\x04\x00\x01")

        client = ModbusClient(_Transport(bytes((0x02, 0x01, 0x01))), unit_id=7, timeout_seconds=1.0)
        discrete_int = client.read_scalar("discrete", 4, "uint16")
        self.assertEqual(discrete_int, 1)
        self.assertIs(type(discrete_int), int)

        client = ModbusClient(_Transport(bytes((0x03, 0x02, 0xFF, 0xFE))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(client.read_scalar("holding", 10, "int16"), -2)

        client = ModbusClient(_Transport(bytes((0x04, 0x04, 0x12, 0x34, 0x56, 0x78))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(client.read_scalar("input", 10, "uint32"), 0x12345678)

        client = ModbusClient(_Transport(bytes((0x04, 0x04, 0x56, 0x78, 0x12, 0x34))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(client.read_scalar("input", 10, "uint32", word_order="little"), 0x12345678)

        client = ModbusClient(_Transport(bytes((0x04, 0x04, 0x12, 0x34, 0x56, 0x78))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ValueError) as invalid_read_scalar_order:
            client.read_scalar("input", 10, "uint32", word_order="middle")
        self.assertEqual(str(invalid_read_scalar_order.exception), "Unsupported Modbus word order 'middle'")

        client = ModbusClient(_Transport(bytes((0x05, 0x00, 0x01, 0xFF, 0x00))), unit_id=7, timeout_seconds=1.0)
        client.write_single_coil(1, True)
        self.assertEqual(client.transport.requests[-1].payload, b"\x00\x01\xff\x00")

        client = ModbusClient(_Transport(bytes((0x05, 0x00, 0x01, 0x00, 0x00))), unit_id=7, timeout_seconds=1.0)
        client.write_single_coil(1, False)
        self.assertEqual(client.transport.requests[-1].payload, b"\x00\x01\x00\x00")

        client = ModbusClient(_Transport(bytes((0x06, 0x00, 0x02, 0x00, 0x09))), unit_id=7, timeout_seconds=1.0)
        client.write_single_register(2, 9)
        self.assertEqual(client.transport.requests[-1].payload, b"\x00\x02\x00\x09")

        client = ModbusClient(_Transport(bytes((0x06, 0x00, 0x02, 0xFF, 0xFE))), unit_id=7, timeout_seconds=1.0)
        client.write_single_register(2, -2)
        self.assertEqual(client.transport.requests[-1].payload, b"\x00\x02\xff\xfe")

        client = ModbusClient(_Transport(bytes((0x10, 0x00, 0x03, 0x00, 0x02))), unit_id=7, timeout_seconds=1.0)
        client.write_multiple_registers(3, [4, 5])
        self.assertEqual(client.transport.requests[-1].payload, b"\x00\x03\x00\x02\x04\x00\x04\x00\x05")

        client = ModbusClient(_Transport(bytes((0x10, 0x00, 0x03, 0x00, 0x02))), unit_id=7, timeout_seconds=1.0)
        client.write_multiple_registers(3, (value for value in (0x10004, -1)))
        self.assertEqual(client.transport.requests[-1].payload, b"\x00\x03\x00\x02\x04\x00\x04\xff\xff")

        client = ModbusClient(_Transport(bytes((0x05, 0x00, 0x01))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as short_coil_write:
            client.write_single_coil(1, True)
        self.assertEqual(str(short_coil_write.exception), "Unexpected Modbus coil write response length")

        client = ModbusClient(_Transport(bytes((0x06, 0x00, 0x02))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as short_register_write:
            client.write_single_register(2, 9)
        self.assertEqual(str(short_register_write.exception), "Unexpected Modbus register write response length")

        client = ModbusClient(_Transport(bytes((0x10, 0x00, 0x03))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaises(ModbusProtocolError) as short_multi_write:
            client.write_multiple_registers(3, [4, 5])
        self.assertEqual(str(short_multi_write.exception), "Unexpected Modbus multi-register write response length")
