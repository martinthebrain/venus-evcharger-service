# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import errno
import termios
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.backend.modbus_transport import (
    ModbusPortOwnershipError,
    ModbusRequest,
    ModbusSerialRtuTransport,
    ModbusTransportSettings,
    ModbusUdpTransport,
    ModbusTcpTransport,
    _VenusSerialPortOwner,
    _configured_serial_attrs,
    _crc_frame,
    _expected_rtu_response_length,
    _modbus_crc,
    _normalized_baudrate,
    _normalized_bytesize,
    _normalized_device,
    _normalized_parity,
    _normalized_port,
    _normalized_retry_count,
    _normalized_retry_delay_seconds,
    _normalized_serial_port_owner,
    _normalized_stopbits,
    _normalized_timeout_seconds,
    _normalized_transport_kind,
    _normalized_unit_id,
    _default_modbus_serial_fields,
    _default_port_owner_fields,
    _optional_transport_command,
    _port_owner_commands,
    _required_host_port,
    _recv_exact,
    _serial_transport_fields,
    _serial_transport_runtime_fields,
    _serial_baudrate_constant,
    _serial_port_owner,
    _transport_runtime_fields,
    create_modbus_transport,
    load_modbus_transport_settings,
    modbus_transport_issue_reason,
    ModbusPortBusyError,
    ModbusSlaveOfflineError,
    ModbusTimeoutError,
    ModbusResponseError,
    ModbusTransportError,
)


class TestShellyWallboxBackendModbusTransportConfig(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(shelly_request_timeout_seconds=2.0)

    @staticmethod
    def _parser(text: str, *, case_sensitive: bool = False) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        if case_sensitive:
            setattr(parser, "optionxform", str)
        parser.read_string(text)
        return parser

    def test_load_modbus_transport_settings_parses_venus_port_owner(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nType=modbus_charger\nTransport=serial_rtu\n[Transport]\nDevice=/dev/ttyS7\nBaudrate=9600\nParity=N\nStopBits=1\nPortOwner=venus\nRetryCount=2\nRetryDelaySeconds=0.5\n")
        settings = load_modbus_transport_settings(parser, self._service())
        self.assertEqual(settings.transport_kind, "serial_rtu")
        self.assertEqual(settings.device, "/dev/ttyS7")
        self.assertEqual(settings.serial_port_owner, "venus_serial_starter")
        self.assertEqual(settings.serial_retry_count, 2)

    def test_venus_serial_port_owner_stops_once_and_releases(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop-tty.sh", "/start-tty.sh")
        completed = SimpleNamespace(returncode=0, stderr="", stdout="")
        with patch("venus_evcharger.backend.modbus_transport_serial.subprocess.run", return_value=completed) as run_mock, patch("venus_evcharger.backend.modbus_transport_serial.atexit.register") as register_mock:
            owner.ensure_owned()
            owner.ensure_owned()
            owner.release()
        self.assertEqual(len(run_mock.call_args_list), 2)
        register_mock.assert_called_once_with(owner.release)

    def test_transport_issue_reason_maps_known_error_types(self) -> None:
        self.assertEqual(modbus_transport_issue_reason(ModbusPortBusyError("busy")), "busy")
        self.assertEqual(modbus_transport_issue_reason(ModbusPortOwnershipError("ownership")), "ownership")
        self.assertEqual(modbus_transport_issue_reason(ModbusSlaveOfflineError("offline")), "offline")
        self.assertEqual(modbus_transport_issue_reason(ModbusTimeoutError("timeout")), "timeout")
        self.assertEqual(modbus_transport_issue_reason(ModbusResponseError("response")), "response")
        self.assertEqual(modbus_transport_issue_reason(ModbusTransportError("transport")), "error")

    def test_normalization_helpers_validate_supported_values(self) -> None:
        self.assertEqual(_normalized_transport_kind("serial"), "serial_rtu")
        self.assertEqual(_normalized_serial_port_owner("something-else"), "none")
        self.assertEqual(_normalized_unit_id("247"), 247)
        self.assertEqual(_normalized_timeout_seconds("1.5", 2.0), 1.5)
        self.assertEqual(_normalized_port("502", 503), 502)
        self.assertEqual(_normalized_device("/dev/ttyS1"), "/dev/ttyS1")
        self.assertEqual(_normalized_baudrate("9600"), 9600)
        self.assertEqual(_normalized_bytesize("8"), 8)
        self.assertEqual(_normalized_parity("e"), "E")
        self.assertEqual(_normalized_stopbits("2"), 2)
        self.assertEqual(_normalized_retry_count("-1", 2), 0)
        self.assertEqual(_normalized_retry_delay_seconds("-1", 0.2), 0.0)

    def test_transport_config_normalizers_cover_all_boundaries_and_aliases(self) -> None:
        self.assertEqual(_normalized_transport_kind(" SERIAL "), "serial_rtu")
        self.assertEqual(_normalized_transport_kind("rtu"), "serial_rtu")
        self.assertEqual(_normalized_transport_kind("serial_rtu"), "serial_rtu")
        self.assertEqual(_normalized_transport_kind("udp"), "udp")
        self.assertEqual(_normalized_transport_kind("tcp"), "tcp")
        self.assertEqual(_normalized_transport_kind("unknown"), "tcp")

        self.assertEqual(_normalized_unit_id(None), 1)
        self.assertEqual(_normalized_unit_id(""), 1)
        self.assertEqual(_normalized_unit_id("0"), 0)
        self.assertEqual(_normalized_unit_id("247"), 247)
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus unit id '-1'$"):
            _normalized_unit_id("-1")
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus unit id '248'$"):
            _normalized_unit_id("248")

        self.assertEqual(_normalized_timeout_seconds(None, 2.0), 2.0)
        self.assertEqual(_normalized_timeout_seconds("0.25", 2.0), 0.25)
        self.assertEqual(_normalized_timeout_seconds("", 2.0), 2.0)
        self.assertEqual(_normalized_timeout_seconds("0", 2.0), 2.0)
        self.assertEqual(_normalized_timeout_seconds("-1", 2.0), 2.0)
        self.assertEqual(_normalized_timeout_seconds("bad", 2.0), 2.0)

        self.assertEqual(_normalized_port(None, 1502), 1502)
        self.assertEqual(_normalized_port("", 1502), 1502)
        self.assertEqual(_normalized_port("1", 1502), 1)
        self.assertEqual(_normalized_port("65535", 1502), 65535)
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus port '0'$"):
            _normalized_port("0", 1502)
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus port '65536'$"):
            _normalized_port("65536", 1502)

        self.assertEqual(_normalized_device(" /dev/ttyUSB0 "), "/dev/ttyUSB0")
        with self.assertRaisesRegex(ValueError, "^Modbus serial_rtu transport requires Transport.Device$"):
            _normalized_device(None)
        with self.assertRaisesRegex(ValueError, "^Modbus serial_rtu transport requires Transport.Device$"):
            _normalized_device("  ")

        self.assertEqual(_normalized_baudrate(None), 9600)
        self.assertEqual(_normalized_baudrate(""), 9600)
        self.assertEqual(_normalized_baudrate("1"), 1)
        self.assertEqual(_normalized_baudrate("19200"), 19200)
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus baudrate '0'$"):
            _normalized_baudrate("0")

        self.assertEqual(_normalized_bytesize(None), 8)
        self.assertEqual(_normalized_bytesize(""), 8)
        for bytesize in (5, 6, 7, 8):
            self.assertEqual(_normalized_bytesize(str(bytesize)), bytesize)
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus bytesize '4'$"):
            _normalized_bytesize("4")
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus bytesize '9'$"):
            _normalized_bytesize("9")

        self.assertEqual(_normalized_parity(None), "N")
        self.assertEqual(_normalized_parity(""), "N")
        self.assertEqual(_normalized_parity("n"), "N")
        self.assertEqual(_normalized_parity("e"), "E")
        self.assertEqual(_normalized_parity("o"), "O")
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus parity 'X'$"):
            _normalized_parity("X")

        self.assertEqual(_normalized_stopbits(None), 1)
        self.assertEqual(_normalized_stopbits(""), 1)
        self.assertEqual(_normalized_stopbits("1"), 1)
        self.assertEqual(_normalized_stopbits("2"), 2)
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus stopbits '0'$"):
            _normalized_stopbits("0")
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus stopbits '3'$"):
            _normalized_stopbits("3")

        for alias in ("venus", "venus_serial_starter", "serial-starter", "victron"):
            self.assertEqual(_normalized_serial_port_owner(alias), "venus_serial_starter")
        self.assertEqual(_normalized_serial_port_owner(None), "none")
        self.assertEqual(_normalized_serial_port_owner("none"), "none")
        self.assertEqual(_normalized_serial_port_owner("other"), "none")

    def test_load_modbus_transport_settings_supports_udp_and_requires_host(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nTransport=udp\n[Transport]\nHost=192.0.2.10\nPort=1502\nUnitId=7\nRequestTimeoutSeconds=3.5\n")
        settings = load_modbus_transport_settings(parser, self._service())
        self.assertEqual(settings.transport_kind, "udp")
        self.assertEqual(settings.host, "192.0.2.10")
        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nTransport=tcp\n[Transport]\nPort=502\n")
        with self.assertRaises(ValueError):
            load_modbus_transport_settings(parser, self._service())

    def test_load_modbus_transport_settings_applies_exact_defaults_and_precedence(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[DEFAULT]\nTransport=tcp\nHost=198.51.100.5\n")
        settings = load_modbus_transport_settings(parser, self._service())
        self.assertEqual(settings.transport_kind, "tcp")
        self.assertEqual(settings.unit_id, 1)
        self.assertEqual(settings.timeout_seconds, 2.0)
        self.assertEqual(settings.host, "198.51.100.5")
        self.assertEqual(settings.port, 502)
        self.assertIsNone(settings.device)
        self.assertEqual(settings.baudrate, 9600)
        self.assertEqual(settings.bytesize, 8)
        self.assertEqual(settings.parity, "N")
        self.assertEqual(settings.stopbits, 1)
        self.assertEqual(settings.serial_port_owner, "none")
        self.assertIsNone(settings.serial_port_owner_stop_command)
        self.assertIsNone(settings.serial_port_owner_start_command)
        self.assertEqual(settings.serial_retry_count, 0)
        self.assertEqual(settings.serial_retry_delay_seconds, 0.2)

        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nTransport=udp\n[Transport]\nType=tcp\nHost=203.0.113.10\nPort=1502\nSlaveId=17\nRequestTimeoutSeconds=4.5\n")
        settings = load_modbus_transport_settings(parser, self._service())
        self.assertEqual(settings.transport_kind, "udp")
        self.assertEqual(settings.unit_id, 17)
        self.assertEqual(settings.timeout_seconds, 4.5)
        self.assertEqual(settings.host, "203.0.113.10")
        self.assertEqual(settings.port, 1502)

    def test_load_modbus_transport_settings_uses_case_sensitive_contract_keys(self) -> None:
        parser = self._parser("[Adapter]\n[Transport]\nType=udp\nHost=192.0.2.55\n", case_sensitive=True)
        settings = load_modbus_transport_settings(parser, SimpleNamespace(shelly_request_timeout_seconds=5.5))
        self.assertEqual(settings.transport_kind, "udp")
        self.assertEqual(settings.timeout_seconds, 5.5)
        self.assertEqual(settings.host, "192.0.2.55")

        parser = self._parser("[Adapter]\nTransport=serial_rtu\n[Transport]\nDevice=/dev/ttyUSB2\n", case_sensitive=True)
        settings = load_modbus_transport_settings(parser, SimpleNamespace(shelly_request_timeout_seconds=0.0))
        self.assertEqual(settings.transport_kind, "serial_rtu")
        self.assertEqual(settings.timeout_seconds, 2.0)
        self.assertEqual(settings.device, "/dev/ttyUSB2")

        parser = self._parser("[Adapter]\nTransport=tcp\n[Transport]\nHost=192.0.2.77\nSlaveId=23\n", case_sensitive=True)
        settings = load_modbus_transport_settings(parser, SimpleNamespace())
        self.assertEqual(settings.unit_id, 23)
        self.assertEqual(settings.host, "192.0.2.77")
        self.assertEqual(settings.timeout_seconds, 2.0)

        parser = self._parser("[Adapter]\nTransport=tcp\n[Transport]\nHost=192.0.2.78\nUnitId=24\n", case_sensitive=True)
        settings = load_modbus_transport_settings(parser, SimpleNamespace())
        self.assertEqual(settings.unit_id, 24)

        parser = self._parser("[Adapter]\nTransport=tcp\n[Transport]\nHost=192.0.2.79\nRequestTimeoutSeconds=4.5\n", case_sensitive=True)
        settings = load_modbus_transport_settings(parser, SimpleNamespace(shelly_request_timeout_seconds=6.5))
        self.assertEqual(settings.timeout_seconds, 4.5)

        parser = self._parser("[Adapter]\nTransport=tcp\n[Transport]\nHost=192.0.2.80\nRequestTimeoutSeconds=bad\n", case_sensitive=True)
        settings = load_modbus_transport_settings(parser, SimpleNamespace(shelly_request_timeout_seconds=6.5))
        self.assertEqual(settings.timeout_seconds, 6.5)

        parser = self._parser(
            "[Adapter]\nTransport=serial_rtu\n"
            "[Transport]\n"
            "Device=/dev/ttyUSB3\n"
            "PortOwner=venus\n"
            "PortOwnerStopCommand=/custom-stop\n"
            "PortOwnerStartCommand=/custom-start\n",
            case_sensitive=True,
        )
        settings = load_modbus_transport_settings(parser, SimpleNamespace())
        self.assertEqual(settings.serial_port_owner_stop_command, "/custom-stop")
        self.assertEqual(settings.serial_port_owner_start_command, "/custom-start")

    def test_serial_transport_field_helpers_compose_exact_runtime_settings(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            "[Transport]\n"
            "Device=/dev/ttyUSB1\n"
            "Baudrate=19200\n"
            "Bytesize=7\n"
            "Parity=E\n"
            "StopBits=2\n"
            "PortOwner=venus\n"
            "PortOwnerStopCommand=/bin/stop\n"
            "PortOwnerStartCommand=/bin/start\n"
            "RetryCount=3\n"
            "RetryDelaySeconds=1.25\n"
        )
        transport = parser["Transport"]
        self.assertEqual(_default_modbus_serial_fields(), (None, None, 9600, 8, "N", 1))
        self.assertEqual(_default_port_owner_fields(), ("none", None, None))
        self.assertEqual(_serial_transport_fields(transport), ("/dev/ttyUSB1", 19200, 7, "E", 2))
        self.assertEqual(_port_owner_commands(transport), ("/bin/stop", "/bin/start"))
        self.assertEqual(
            _serial_transport_runtime_fields(transport),
            ("/dev/ttyUSB1", 19200, 7, "E", 2, "venus_serial_starter", "/bin/stop", "/bin/start", 3, 1.25),
        )

        blank_parser = configparser.ConfigParser()
        blank_parser.read_string("[Transport]\nPortOwnerStopCommand=   \nPortOwnerStartCommand=   \n")
        blank_transport = blank_parser["Transport"]
        self.assertIsNone(_optional_transport_command(blank_transport, "PortOwnerStopCommand", "/default-stop"))
        self.assertEqual(_optional_transport_command(blank_transport, "Missing", "/default"), "/default")

    def test_serial_transport_helpers_use_exact_case_sensitive_keys_and_defaults(self) -> None:
        parser = self._parser("[Transport]\nDevice=/dev/ttyUSB4\n", case_sensitive=True)
        transport = parser["Transport"]
        self.assertEqual(_serial_transport_fields(transport), ("/dev/ttyUSB4", 9600, 8, "N", 1))
        self.assertEqual(
            _serial_transport_runtime_fields(transport),
            (
                "/dev/ttyUSB4",
                9600,
                8,
                "N",
                1,
                "none",
                "/opt/victronenergy/serial-starter/stop-tty.sh",
                "/opt/victronenergy/serial-starter/start-tty.sh",
                1,
                0.2,
            ),
        )

        parser = self._parser("[Transport]\n", case_sensitive=True)
        with self.assertRaisesRegex(ValueError, "^Modbus serial_rtu transport requires Transport.Device$"):
            _serial_transport_fields(parser["Transport"])

        parser = self._parser(
            "[Transport]\n"
            "Device=/dev/ttyUSB5\n"
            "Baudrate=38400\n"
            "Bytesize=6\n"
            "Parity=O\n"
            "StopBits=2\n"
            "PortOwner=venus\n"
            "RetryCount=4\n"
            "RetryDelaySeconds=0.75\n",
            case_sensitive=True,
        )
        transport = parser["Transport"]
        self.assertEqual(_serial_transport_fields(transport), ("/dev/ttyUSB5", 38400, 6, "O", 2))
        self.assertEqual(_serial_transport_runtime_fields(transport)[5], "venus_serial_starter")
        self.assertEqual(_serial_transport_runtime_fields(transport)[8:], (4, 0.75))

        parser = self._parser(
            "[Transport]\n"
            "Device=/dev/ttyUSB6\n"
            "RetryCount=bad\n"
            "RetryDelaySeconds=bad\n",
            case_sensitive=True,
        )
        self.assertEqual(_serial_transport_runtime_fields(parser["Transport"])[8:], (1, 0.2))

    def test_port_owner_commands_use_exact_case_sensitive_keys_and_defaults(self) -> None:
        parser = self._parser("[Transport]\n", case_sensitive=True)
        self.assertEqual(
            _port_owner_commands(parser["Transport"]),
            (
                "/opt/victronenergy/serial-starter/stop-tty.sh",
                "/opt/victronenergy/serial-starter/start-tty.sh",
            ),
        )

        parser = self._parser(
            "[Transport]\nPortOwnerStopCommand=/case-stop\nPortOwnerStartCommand=/case-start\n",
            case_sensitive=True,
        )
        self.assertEqual(_port_owner_commands(parser["Transport"]), ("/case-stop", "/case-start"))

    def test_transport_runtime_fields_and_required_host_port_are_exact(self) -> None:
        parser = self._parser("[Transport]\nHost=192.0.2.20\nPort=1502\n", case_sensitive=True)
        transport = parser["Transport"]
        self.assertEqual(_required_host_port("tcp", transport, "192.0.2.20"), 1502)
        with self.assertRaisesRegex(ValueError, "^Modbus udp transport requires Transport.Host$"):
            _required_host_port("udp", transport, None)
        self.assertEqual(
            _transport_runtime_fields("tcp", transport, "192.0.2.20"),
            (1502, None, 9600, 8, "N", 1, "none", None, None, 0, 0.2),
        )

        no_port_parser = self._parser("[Transport]\nHost=192.0.2.21\n", case_sensitive=True)
        self.assertEqual(_required_host_port("tcp", no_port_parser["Transport"], "192.0.2.21"), 502)
        blank_port_parser = self._parser("[Transport]\nHost=192.0.2.22\nPort=   \n", case_sensitive=True)
        self.assertEqual(_required_host_port("tcp", blank_port_parser["Transport"], "192.0.2.22"), 502)
        with self.assertRaisesRegex(ValueError, "^Modbus udp transport requires Transport.Host$"):
            _transport_runtime_fields("udp", no_port_parser["Transport"], None)

    def test_crc_helpers_and_expected_rtu_response_length_cover_supported_shapes(self) -> None:
        payload = b"\x01\x03\x00\x00\x00\x01"
        framed = _crc_frame(payload)
        self.assertEqual(_modbus_crc(payload), framed[-2] | (framed[-1] << 8))
        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x03\x02"), 7)
        self.assertEqual(_expected_rtu_response_length(0x06, b"\x01\x06\x00"), 8)

    def test_serial_baudrate_and_configured_attrs_cover_parity_and_stopbits(self) -> None:
        self.assertIsInstance(_serial_baudrate_constant(9600), int)
        settings = ModbusTransportSettings(transport_kind="serial_rtu", unit_id=1, timeout_seconds=1.0, host=None, port=None, device="/dev/ttyS7", baudrate=9600, bytesize=7, parity="E", stopbits=2, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        cc = [0] * (max(termios.VMIN, termios.VTIME) + 1)
        with patch("venus_evcharger.backend.modbus_transport_serial.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, cc.copy()]):
            attrs = _configured_serial_attrs(3, settings)
        self.assertTrue(attrs[2] & termios.PARENB)
        self.assertTrue(attrs[2] & termios.CSTOPB)

    def test_venus_serial_port_owner_handles_failures_and_optional_release(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        with patch("venus_evcharger.backend.modbus_transport_serial.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(ModbusPortOwnershipError):
                owner.ensure_owned()
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        with patch.object(owner, "_run_command") as run_command:
            owner.release()
            owner.recover()
        run_command.assert_not_called()

    def test_serial_port_owner_factory_validates_required_stop_command(self) -> None:
        tcp_settings = ModbusTransportSettings(transport_kind="tcp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        self.assertIsNone(_serial_port_owner(tcp_settings))
        bad_settings = ModbusTransportSettings(transport_kind="serial_rtu", unit_id=1, timeout_seconds=1.0, host=None, port=None, device="/dev/ttyS7", baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="venus_serial_starter", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        with self.assertRaisesRegex(
            ValueError,
            "^Modbus serial port ownership requires Transport.PortOwnerStopCommand$",
        ):
            _serial_port_owner(bad_settings)

    def test_recv_exact_collects_multiple_chunks_and_detects_disconnect(self) -> None:
        sock = unittest.mock.MagicMock()
        sock.recv.side_effect = [b"\x01", b"\x02\x03"]
        self.assertEqual(_recv_exact(sock, 3), b"\x01\x02\x03")
        sock = unittest.mock.MagicMock()
        sock.recv.side_effect = [b"\x01", b""]
        with self.assertRaises(TimeoutError):
            _recv_exact(sock, 2)

    def test_transport_normalizers_cover_invalid_edges_and_default_fallbacks(self) -> None:
        self.assertEqual(_normalized_timeout_seconds("bad", 2.5), 2.5)
        self.assertEqual(_normalized_timeout_seconds("0", 2.5), 2.5)
        self.assertEqual(_normalized_retry_count("bad", 3), 3)
        self.assertEqual(_normalized_retry_delay_seconds("bad", 0.4), 0.4)
        self.assertEqual(_normalized_transport_kind("udp"), "udp")
        self.assertEqual(_normalized_serial_port_owner("victron"), "venus_serial_starter")

        with self.assertRaises(ValueError):
            _normalized_unit_id("248")
        with self.assertRaises(ValueError):
            _normalized_port("70000", 502)
        with self.assertRaises(ValueError):
            _normalized_device("   ")
        with self.assertRaises(ValueError):
            _normalized_baudrate("0")
        with self.assertRaises(ValueError):
            _normalized_bytesize("9")
        with self.assertRaises(ValueError):
            _normalized_parity("X")
        with self.assertRaises(ValueError):
            _normalized_stopbits("3")

    def test_venus_serial_port_owner_release_and_baudrate_helper_cover_remaining_branches(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        owner._owned = True
        with patch.object(owner, "_run_command", side_effect=ModbusPortOwnershipError("boom")):
            owner.release()
        self.assertTrue(owner._owned)

        failing_owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        failed = SimpleNamespace(returncode=1, stderr="bad", stdout="")
        with patch("venus_evcharger.backend.modbus_transport_serial.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(ModbusPortOwnershipError, "bad"):
                failing_owner.ensure_owned()

        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus serial baudrate '12345'$"):
            _serial_baudrate_constant(12345)
