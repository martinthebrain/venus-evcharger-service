# SPDX-License-Identifier: GPL-3.0-or-later
import errno
import os
import socket
import termios
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.modbus_transport import (
    ModbusPortBusyError,
    ModbusPortOwnershipError,
    ModbusRequest,
    ModbusResponseError,
    ModbusSerialRtuTransport,
    ModbusSlaveOfflineError,
    ModbusTcpTransport,
    ModbusTimeoutError,
    ModbusTransportError,
    ModbusTransportSettings,
    ModbusUdpTransport,
    _VenusSerialPortOwner,
    _configured_serial_attrs,
    _serial_port_owner,
    _crc_frame,
    _expected_rtu_response_length,
    _modbus_crc,
    create_modbus_transport,
)


class TestShellyWallboxBackendModbusTransportRuntime(unittest.TestCase):
    def _serial_settings(self, retry_count=0, retry_delay=0.0, owner="none") -> ModbusTransportSettings:
        return ModbusTransportSettings(transport_kind="serial_rtu", unit_id=1, timeout_seconds=1.0, host=None, port=None, device="/dev/ttyS7", baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner=owner, serial_port_owner_stop_command="/stop.sh" if owner != "none" else None, serial_port_owner_start_command="/start.sh" if owner != "none" else None, serial_retry_count=retry_count, serial_retry_delay_seconds=retry_delay)

    def _network_settings(self, kind: str) -> ModbusTransportSettings:
        return ModbusTransportSettings(transport_kind=kind, unit_id=7, timeout_seconds=2.25, host="192.0.2.44", port=1502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)

    def test_crc_uses_standard_modbus_vectors(self) -> None:
        first_request = b"\x01\x03\x00\x00\x00\x0a"
        second_request = b"\x11\x04\x00\x08\x00\x01"
        self.assertEqual(_modbus_crc(first_request), 0xCDC5)
        self.assertEqual(_crc_frame(first_request), first_request + b"\xc5\xcd")
        self.assertEqual(_modbus_crc(second_request), 0x98B2)
        self.assertEqual(_crc_frame(second_request), second_request + b"\xb2\x98")

    def test_configured_serial_attrs_exactly_reset_and_apply_flags(self) -> None:
        settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=7,
            parity="E",
            stopbits=2,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        cc = [9] * (max(termios.VMIN, termios.VTIME) + 1)
        with patch(
            "venus_evcharger.backend.modbus_transport.termios.tcgetattr",
            return_value=[111, 222, 333, 444, 555, 666, cc],
        ) as get_attrs:
            attrs = _configured_serial_attrs(17, settings)
        get_attrs.assert_called_once_with(17)
        self.assertEqual(attrs[0], 0)
        self.assertEqual(attrs[1], 0)
        self.assertEqual(attrs[2], termios.CREAD | termios.CLOCAL | termios.CS7 | termios.PARENB | termios.CSTOPB)
        self.assertEqual(attrs[3], 0)
        self.assertEqual(attrs[4], termios.B9600)
        self.assertEqual(attrs[5], termios.B9600)
        self.assertEqual(attrs[6][termios.VMIN], 0)
        self.assertEqual(attrs[6][termios.VTIME], 0)

    def test_configured_serial_attrs_exactly_maps_bytesize_and_parity_variants(self) -> None:
        base = self._serial_settings().__dict__
        cases = [
            (5, "N", 1, termios.CS5),
            (6, "N", 1, termios.CS6),
            (8, "O", 1, termios.CS8 | termios.PARENB | termios.PARODD),
        ]
        for bytesize, parity, stopbits, expected_extra in cases:
            settings = ModbusTransportSettings(**{**base, "bytesize": bytesize, "parity": parity, "stopbits": stopbits})
            cc = [8] * (max(termios.VMIN, termios.VTIME) + 1)
            with self.subTest(bytesize=bytesize, parity=parity):
                with patch(
                    "venus_evcharger.backend.modbus_transport.termios.tcgetattr",
                    return_value=[7, 6, termios.CSIZE | termios.PARENB | termios.PARODD | termios.CSTOPB, 5, 4, 3, cc],
                ):
                    attrs = _configured_serial_attrs(19, settings)
            expected_cflag = termios.CREAD | termios.CLOCAL | expected_extra
            self.assertEqual(attrs[0], 0)
            self.assertEqual(attrs[1], 0)
            self.assertEqual(attrs[2], expected_cflag)
            self.assertEqual(attrs[3], 0)
            self.assertEqual(attrs[4], termios.B9600)
            self.assertEqual(attrs[5], termios.B9600)

    def test_tcp_transport_uses_exact_mbap_frames_and_connection_contract(self) -> None:
        settings = self._network_settings("tcp")
        transport = ModbusTcpTransport(settings)
        first_sock = MagicMock()
        first_sock.__enter__.return_value = first_sock
        first_sock.__exit__.return_value = False
        second_sock = MagicMock()
        second_sock.__enter__.return_value = second_sock
        second_sock.__exit__.return_value = False
        request = ModbusRequest(7, 0x10, b"\x00\x01\x00\x02\x04\x00\x05\x00\x06")
        response_header = b"\x00\x01\x00\x00\x00\x06\x07"
        response_body = b"\x10\x00\x01\x00\x02"
        with (
            patch(
                "venus_evcharger.backend.modbus_transport.socket.create_connection",
                side_effect=[first_sock, second_sock],
            ) as create_connection,
            patch(
                "venus_evcharger.backend.modbus_transport._recv_exact",
                side_effect=[response_header, response_body, response_header, response_body],
            ) as recv_exact,
        ):
            self.assertEqual(transport.exchange(request, timeout_seconds=2.25), response_body)
            self.assertEqual(transport.exchange(request, timeout_seconds=2.25), response_body)
        create_connection.assert_any_call(("192.0.2.44", 1502), timeout=2.25)
        self.assertEqual(create_connection.call_count, 2)
        first_sock.settimeout.assert_called_once_with(2.25)
        second_sock.settimeout.assert_called_once_with(2.25)
        first_sock.sendall.assert_called_once_with(b"\x00\x01\x00\x00\x00\x0b\x07\x10\x00\x01\x00\x02\x04\x00\x05\x00\x06")
        second_sock.sendall.assert_called_once_with(b"\x00\x02\x00\x00\x00\x0b\x07\x10\x00\x01\x00\x02\x04\x00\x05\x00\x06")
        self.assertEqual([call.args[1] for call in recv_exact.call_args_list], [7, 5, 7, 5])
        self.assertIs(recv_exact.call_args_list[0].args[0], first_sock)
        self.assertIs(recv_exact.call_args_list[1].args[0], first_sock)
        self.assertIs(recv_exact.call_args_list[2].args[0], second_sock)
        self.assertIs(recv_exact.call_args_list[3].args[0], second_sock)

    def test_tcp_transport_respects_large_and_empty_mbap_lengths(self) -> None:
        transport = ModbusTcpTransport(self._network_settings("tcp"))
        transport._transaction_id = 0x7FFF
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False
        large_body = b"x" * 32768
        with (
            patch("venus_evcharger.backend.modbus_transport.socket.create_connection", return_value=sock),
            patch(
                "venus_evcharger.backend.modbus_transport._recv_exact",
                side_effect=[b"\x80\x00\x00\x00\x80\x01\x07", large_body, b"\x80\x01\x00\x00\x00\x01\x07", b""],
            ) as recv_exact,
        ):
            self.assertEqual(transport.exchange(ModbusRequest(7, 0x03, b"x" * 32767), timeout_seconds=2.25), large_body)
            self.assertEqual(transport.exchange(ModbusRequest(7, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=2.25), b"")
        first_frame = sock.sendall.call_args_list[0].args[0]
        self.assertEqual(first_frame[:7], b"\x80\x00\x00\x00\x80\x01\x07")
        self.assertEqual(len(first_frame), 32775)
        self.assertEqual([call.args[1] for call in recv_exact.call_args_list], [7, 32768, 7, 0])
        self.assertIs(recv_exact.call_args_list[0].args[0], sock)
        self.assertIs(recv_exact.call_args_list[1].args[0], sock)
        self.assertIs(recv_exact.call_args_list[2].args[0], sock)
        self.assertIs(recv_exact.call_args_list[3].args[0], sock)

    def test_udp_transport_uses_exact_mbap_frame_and_datagram_contract(self) -> None:
        settings = self._network_settings("udp")
        transport = ModbusUdpTransport(settings)
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False
        sock.recvfrom.side_effect = [
            (b"\x00\x01\x00\x00\x00\x06\x07\x03\x02\x00\xa0", ("192.0.2.44", 1502)),
            (b"\x00\x02\x00\x00\x00\x06\x07\x03\x02\x00\xa1", ("192.0.2.44", 1502)),
        ]
        request = ModbusRequest(7, 0x03, b"\x00\x00\x00\x01")
        with patch("venus_evcharger.backend.modbus_transport.socket.socket", return_value=sock) as socket_factory:
            self.assertEqual(transport.exchange(request, timeout_seconds=2.25), b"\x03\x02\x00\xa0")
            self.assertEqual(transport.exchange(request, timeout_seconds=2.25), b"\x03\x02\x00\xa1")
        socket_factory.assert_called_with(socket.AF_INET, socket.SOCK_DGRAM)
        self.assertEqual(sock.settimeout.call_args_list[0].args, (2.25,))
        self.assertEqual(
            sock.sendto.call_args_list[0].args,
            (b"\x00\x01\x00\x00\x00\x06\x07\x03\x00\x00\x00\x01", ("192.0.2.44", 1502)),
        )
        self.assertEqual(
            sock.sendto.call_args_list[1].args,
            (b"\x00\x02\x00\x00\x00\x06\x07\x03\x00\x00\x00\x01", ("192.0.2.44", 1502)),
        )
        self.assertEqual(sock.recvfrom.call_args_list[0].args, (260,))

    def test_udp_transport_respects_boundary_large_and_empty_mbap_lengths(self) -> None:
        transport = ModbusUdpTransport(self._network_settings("udp"))
        transport._transaction_id = 0x7FFF
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False
        large_body = b"y" * 32768
        sock.recvfrom.side_effect = [
            (b"\x80\x00\x00\x00\x80\x01\x07" + large_body, ("192.0.2.44", 1502)),
            (b"\x80\x01\x00\x00\x00\x01\x07Z", ("192.0.2.44", 1502)),
            (b"\x80\x02\x00\x00\x00\x01\x07", ("192.0.2.44", 1502)),
            (b"\x80\x03", ("192.0.2.44", 1502)),
        ]
        with patch("venus_evcharger.backend.modbus_transport.socket.socket", return_value=sock):
            self.assertEqual(transport.exchange(ModbusRequest(7, 0x03, b"y" * 32767), timeout_seconds=2.25), large_body)
            self.assertEqual(transport.exchange(ModbusRequest(7, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=2.25), b"")
            self.assertEqual(transport.exchange(ModbusRequest(7, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=2.25), b"")
            with self.assertRaisesRegex(TimeoutError, "^Incomplete Modbus UDP response$"):
                transport.exchange(ModbusRequest(7, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=2.25)
        first_frame = sock.sendto.call_args_list[0].args[0]
        self.assertEqual(first_frame[:7], b"\x80\x00\x00\x00\x80\x01\x07")
        self.assertEqual(len(first_frame), 32775)

    def test_connection_recv_exact_uses_remaining_byte_count_and_stable_error(self) -> None:
        sock = MagicMock()
        sock.recv.side_effect = [b"\x01\x02", b"\x03\x04"]
        from venus_evcharger.backend.modbus_transport import _recv_exact

        self.assertEqual(_recv_exact(sock, 4), b"\x01\x02\x03\x04")
        self.assertEqual([call.args for call in sock.recv.call_args_list], [(4,), (2,)])

        closed_sock = MagicMock()
        closed_sock.recv.side_effect = [b"\x01", b""]
        with self.assertRaisesRegex(TimeoutError, "^Modbus transport closed before full response was received$"):
            _recv_exact(closed_sock, 2)

    def test_expected_rtu_response_length_covers_all_supported_function_groups(self) -> None:
        self.assertEqual(_expected_rtu_response_length(0x01, b"\x01\x01\x01"), 6)
        self.assertEqual(_expected_rtu_response_length(0x02, b"\x01\x02\x02"), 7)
        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x03\x03"), 8)
        self.assertEqual(_expected_rtu_response_length(0x04, b"\x01\x04\x04"), 9)
        self.assertEqual(_expected_rtu_response_length(0x05, b"\x01\x05\x00"), 8)
        self.assertEqual(_expected_rtu_response_length(0x06, b"\x01\x06\x00"), 8)
        self.assertEqual(_expected_rtu_response_length(0x0F, b"\x01\x0f\x00"), 8)
        self.assertEqual(_expected_rtu_response_length(0x10, b"\x01\x10\x00"), 8)
        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x83\x02"), 5)
        with self.assertRaisesRegex(ValueError, "^Incomplete Modbus RTU header$"):
            _expected_rtu_response_length(0x03, b"\x01\x03")
        with self.assertRaisesRegex(ValueError, "^Unsupported Modbus RTU function code 0x07$"):
            _expected_rtu_response_length(0x07, b"\x01\x07\x02")

    def test_port_owner_command_helpers_use_exact_subprocess_contract(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        completed = SimpleNamespace(returncode=0, stderr="stderr detail\n", stdout="stdout detail\n")
        with patch("venus_evcharger.backend.modbus_transport.subprocess.run", return_value=completed) as run_mock:
            self.assertIs(owner._command_result("/stop.sh"), completed)
        run_mock.assert_called_once_with(
            ["/stop.sh", "/dev/ttyS7"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(owner._command_detail(completed), "stderr detail")
        self.assertEqual(owner._command_detail(SimpleNamespace(stderr="", stdout="stdout detail\n")), "stdout detail")
        self.assertEqual(owner._command_detail(SimpleNamespace(stderr="", stdout="")), "")

    def test_port_owner_lifecycle_and_factory_keep_exact_state(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        self.assertEqual(owner.device, "/dev/ttyS7")
        self.assertEqual(owner.stop_command, "/stop.sh")
        self.assertEqual(owner.start_command, "/start.sh")
        self.assertIs(owner._owned, False)
        self.assertIs(owner._release_registered, False)

        with patch.object(owner, "_run_command") as run_command, patch(
            "venus_evcharger.backend.modbus_transport.atexit.register"
        ) as register:
            owner.ensure_owned()
            owner.ensure_owned()
            owner.release()
        self.assertEqual([call.args for call in run_command.call_args_list], [("/stop.sh",), ("/start.sh",)])
        register.assert_called_once_with(owner.release)
        self.assertIs(owner._owned, False)
        self.assertIs(owner._release_registered, True)

        owner_without_start = _VenusSerialPortOwner("/dev/ttyS8", "/stop.sh", None)
        with patch.object(owner_without_start, "_run_command") as run_without_start, patch(
            "venus_evcharger.backend.modbus_transport.atexit.register"
        ) as register_without_start:
            owner_without_start.ensure_owned()
        run_without_start.assert_called_once_with("/stop.sh")
        register_without_start.assert_not_called()

        idle_owner = _VenusSerialPortOwner("/dev/ttyS9", "/stop.sh", "/start.sh")
        with patch.object(idle_owner, "_run_command") as idle_run:
            idle_owner.release()
        idle_run.assert_not_called()

        settings = self._serial_settings(owner="venus_serial_starter")
        created = _serial_port_owner(settings)
        assert created is not None
        self.assertEqual(created.device, "/dev/ttyS7")
        self.assertEqual(created.stop_command, "/stop.sh")
        self.assertEqual(created.start_command, "/start.sh")
        self.assertIsNotNone(ModbusSerialRtuTransport(settings)._port_owner)

    def test_port_owner_run_command_uses_exact_command_and_error_messages(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        successful = SimpleNamespace(returncode=0, stderr="", stdout="")
        with patch.object(owner, "_command_result", return_value=successful) as command_result:
            owner._run_command("/stop.sh")
        command_result.assert_called_once_with("/stop.sh")

        missing_owner = _VenusSerialPortOwner("/dev/ttyS7", "/missing.sh", None)
        with patch.object(missing_owner, "_command_result", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(
                ModbusPortOwnershipError,
                "^Venus serial ownership helper '/missing.sh' is unavailable for /dev/ttyS7$",
            ):
                missing_owner._run_command("/missing.sh")

        failing_owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        with patch.object(
            failing_owner,
            "_command_result",
            return_value=SimpleNamespace(returncode=1, stderr="", stdout=""),
        ):
            with self.assertRaisesRegex(
                ModbusPortOwnershipError,
                "^Venus serial ownership helper '/stop.sh' failed for /dev/ttyS7$",
            ):
                failing_owner._run_command("/stop.sh")

    def test_serial_rtu_transport_retries_timeout_once(self) -> None:
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=1))
        with patch.object(transport, "_exchange_once", side_effect=[ModbusTimeoutError("timeout"), b"\x03\x02\x00\xa0"]) as exchange_mock:
            response = transport.exchange(request, timeout_seconds=1.0)
        self.assertEqual(response, b"\x03\x02\x00\xa0")
        self.assertEqual(exchange_mock.call_count, 2)

    def test_serial_rtu_exchange_orchestrates_retry_inputs_and_recovery_error(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=2))
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        timeout_error = ModbusTimeoutError("timeout")
        with (
            patch.object(transport, "_ensure_port_owned") as ensure_owned,
            patch.object(transport, "_exchange_attempt", side_effect=[(None, timeout_error), (b"\x03\x02\x00\xa0", None)]) as attempt,
            patch.object(transport, "_recover_after_failure") as recover,
        ):
            self.assertEqual(transport.exchange(request, timeout_seconds=1.25), b"\x03\x02\x00\xa0")
        self.assertEqual(ensure_owned.call_count, 2)
        self.assertEqual([call.args for call in attempt.call_args_list], [(request, 1.25), (request, 1.25)])
        recover.assert_called_once_with(timeout_error)

    def test_serial_rtu_attempt_helpers_have_exact_retry_contract(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=2))
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        with patch.object(transport, "_exchange_once", return_value=b"\x03\x02\x00\xa0") as once:
            result, error = transport._exchange_attempt(request, 1.25)
        self.assertEqual(result, b"\x03\x02\x00\xa0")
        self.assertIsNone(error)
        once.assert_called_once_with(request, 1.25)
        self.assertEqual(ModbusSerialRtuTransport(self._serial_settings(retry_count=0))._serial_attempt_count(), 1)
        self.assertEqual(transport._serial_attempt_count(), 3)
        self.assertFalse(ModbusSerialRtuTransport._serial_retry_exhausted(0, 2))
        self.assertTrue(ModbusSerialRtuTransport._serial_retry_exhausted(1, 2))
        final_error = transport._final_serial_exchange_error(request, ModbusTimeoutError("timeout"))
        self.assertIsInstance(final_error, ModbusSlaveOfflineError)
        self.assertEqual(str(final_error), "Modbus slave 1 on /dev/ttyS7 did not respond")

    def test_serial_rtu_transport_raises_slave_offline_after_retries(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=1))
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        with patch.object(transport, "_exchange_once", side_effect=ModbusTimeoutError("timeout")):
            with self.assertRaises(ModbusSlaveOfflineError):
                transport.exchange(request, timeout_seconds=1.0)

    def test_serial_rtu_transport_maps_busy_os_errors(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings())
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        with patch.object(transport, "_exchange_once", side_effect=OSError(errno.EBUSY, "busy")):
            with self.assertRaises(ModbusPortBusyError):
                transport.exchange(request, timeout_seconds=1.0)

    def test_tcp_and_udp_transports_exchange_with_mbap_framing(self) -> None:
        settings = ModbusTransportSettings(transport_kind="tcp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        transport = ModbusTcpTransport(settings)
        fake_sock = MagicMock()
        fake_sock.__enter__.return_value = fake_sock
        fake_sock.__exit__.return_value = False
        with patch("venus_evcharger.backend.modbus_transport.socket.create_connection", return_value=fake_sock), patch("venus_evcharger.backend.modbus_transport._recv_exact", side_effect=[b"\x00\x01\x00\x00\x00\x05\x01", b"\x03\x02\x00\xa0"]):
            response = transport.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0)
        self.assertEqual(response, b"\x03\x02\x00\xa0")

        udp_settings = ModbusTransportSettings(transport_kind="udp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        transport = ModbusUdpTransport(udp_settings)
        udp_sock = MagicMock()
        udp_sock.__enter__.return_value = udp_sock
        udp_sock.__exit__.return_value = False
        udp_sock.recvfrom.return_value = (b"\x00\x01\x00\x00\x00\x05\x01\x03\x02\x00\xa0", ("127.0.0.1", 502))
        with patch("venus_evcharger.backend.modbus_transport.socket.socket", return_value=udp_sock):
            self.assertEqual(transport.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0), b"\x03\x02\x00\xa0")

    def test_serial_rtu_exchange_once_validates_crc_and_transport_helpers(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings())
        request = ModbusRequest(1, 0x03, b"\x00\x00\x00\x01")
        response_payload = b"\x01\x03\x02\x00\xa0"
        response_frame = _crc_frame(response_payload)
        with patch("venus_evcharger.backend.modbus_transport.os.open", return_value=5), patch("venus_evcharger.backend.modbus_transport.os.close"), patch("venus_evcharger.backend.modbus_transport.termios.tcsetattr"), patch("venus_evcharger.backend.modbus_transport.termios.tcflush"), patch("venus_evcharger.backend.modbus_transport._configured_serial_attrs", return_value=[]), patch.object(transport, "_write_all"), patch.object(transport, "_read_exact", side_effect=[response_frame[:3], response_frame[3:]]):
            response = transport._exchange_once(request, 1.0)
        self.assertEqual(response, response_payload[1:])

    def test_serial_rtu_exchange_once_uses_exact_file_termios_and_frame_contract(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings())
        request = ModbusRequest(1, 0x03, b"\x00\x00\x00\x01")
        response_frame = _crc_frame(b"\x01\x03\x02\x00\xa0")
        expected_frame = _crc_frame(b"\x01\x03\x00\x00\x00\x01")
        attrs = [0, 0, 0, 0, 0, 0, [0, 0]]
        with (
            patch("venus_evcharger.backend.modbus_transport.os.open", return_value=23) as open_mock,
            patch("venus_evcharger.backend.modbus_transport.os.close") as close_mock,
            patch("venus_evcharger.backend.modbus_transport._configured_serial_attrs", return_value=attrs) as attrs_mock,
            patch("venus_evcharger.backend.modbus_transport.termios.tcsetattr") as set_attrs,
            patch("venus_evcharger.backend.modbus_transport.termios.tcflush") as flush_mock,
            patch.object(transport, "_write_all") as write_all,
            patch.object(transport, "_read_exact", side_effect=[response_frame[:3], response_frame[3:]]) as read_exact,
        ):
            self.assertEqual(transport._exchange_once(request, 1.75), b"\x03\x02\x00\xa0")
        open_mock.assert_called_once_with("/dev/ttyS7", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs_mock.assert_called_once_with(23, transport.settings)
        set_attrs.assert_called_once_with(23, termios.TCSANOW, attrs)
        flush_mock.assert_called_once_with(23, termios.TCIOFLUSH)
        write_all.assert_called_once_with(23, expected_frame)
        self.assertEqual([call.args for call in read_exact.call_args_list], [(23, 3, 1.75), (23, 4, 1.75)])
        close_mock.assert_called_once_with(23)

    def test_serial_rtu_exchange_once_accepts_minimal_exception_frame_and_stable_errors(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings())
        request = ModbusRequest(1, 0x03, b"\x00\x00\x00\x01")
        exception_frame = _crc_frame(b"\x01\x83\x02")
        with (
            patch("venus_evcharger.backend.modbus_transport.os.open", return_value=23),
            patch("venus_evcharger.backend.modbus_transport.os.close"),
            patch("venus_evcharger.backend.modbus_transport._configured_serial_attrs", return_value=[]),
            patch("venus_evcharger.backend.modbus_transport.termios.tcsetattr"),
            patch("venus_evcharger.backend.modbus_transport.termios.tcflush"),
            patch.object(transport, "_write_all"),
            patch.object(transport, "_read_exact", side_effect=[exception_frame[:3], exception_frame[3:]]),
        ):
            self.assertEqual(transport._exchange_once(request, 1.0), b"\x83\x02")

        with (
            patch("venus_evcharger.backend.modbus_transport.os.open", return_value=23),
            patch("venus_evcharger.backend.modbus_transport.os.close"),
            patch("venus_evcharger.backend.modbus_transport._configured_serial_attrs", return_value=[]),
            patch("venus_evcharger.backend.modbus_transport.termios.tcsetattr"),
            patch("venus_evcharger.backend.modbus_transport.termios.tcflush"),
            patch.object(transport, "_write_all"),
            patch.object(transport, "_read_exact", side_effect=[b"\x01\x03\x00", b"\x00"]),
        ):
            with self.assertRaisesRegex(ModbusTimeoutError, "^Incomplete Modbus RTU response$"):
                transport._exchange_once(request, 1.0)

        with (
            patch("venus_evcharger.backend.modbus_transport.os.open", return_value=23),
            patch("venus_evcharger.backend.modbus_transport.os.close"),
            patch("venus_evcharger.backend.modbus_transport._configured_serial_attrs", return_value=[]),
            patch("venus_evcharger.backend.modbus_transport.termios.tcsetattr"),
            patch("venus_evcharger.backend.modbus_transport.termios.tcflush"),
            patch.object(transport, "_write_all"),
            patch.object(transport, "_read_exact", side_effect=[b"\x01\x03\x02", b"\x00\xa0\x00\x00"]),
        ):
            with self.assertRaisesRegex(ModbusResponseError, "^Invalid Modbus RTU CRC$"):
                transport._exchange_once(request, 1.0)

    def test_serial_rtu_write_and_read_helpers_cover_partial_io_and_timeouts(self) -> None:
        with patch("venus_evcharger.backend.modbus_transport.os.write", side_effect=[2, 2]):
            ModbusSerialRtuTransport._write_all(5, b"abcd")
        with patch("venus_evcharger.backend.modbus_transport.select.select", return_value=([5], [], [])), patch("venus_evcharger.backend.modbus_transport.os.read", side_effect=[b"\x01", b"\x02"]), patch("venus_evcharger.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0, 0.1]):
            self.assertEqual(ModbusSerialRtuTransport._read_exact(5, 2, 1.0), b"\x01\x02")

    def test_serial_write_all_and_read_exact_use_precise_remaining_lengths(self) -> None:
        from venus_evcharger.backend.modbus_transport import _positive_serial_write_count

        self.assertEqual(_positive_serial_write_count(1), 1)
        with self.assertRaisesRegex(ModbusPortBusyError, "^Failed to write full Modbus RTU request$"):
            _positive_serial_write_count(0)
        with self.assertRaisesRegex(ModbusPortBusyError, "^Failed to write full Modbus RTU request$"):
            _positive_serial_write_count(-1)

        with patch("venus_evcharger.backend.modbus_transport.os.write", side_effect=[2, 3, 1]) as write_mock:
            ModbusSerialRtuTransport._write_all(9, b"abcdef")
        self.assertEqual([call.args for call in write_mock.call_args_list], [(9, b"abcdef"), (9, b"cdef"), (9, b"f")])

        with patch("venus_evcharger.backend.modbus_transport.os.write", return_value=0):
            with self.assertRaisesRegex(ModbusPortBusyError, "^Failed to write full Modbus RTU request$"):
                ModbusSerialRtuTransport._write_all(9, b"ab")

        with (
            patch("venus_evcharger.backend.modbus_transport.time.monotonic", side_effect=[10.0, 10.25, 10.75]),
            patch("venus_evcharger.backend.modbus_transport.select.select", return_value=([9], [], [])) as select_mock,
            patch("venus_evcharger.backend.modbus_transport.os.read", side_effect=[b"\x01", b"\x02\x03"]) as read_mock,
        ):
            self.assertEqual(ModbusSerialRtuTransport._read_exact(9, 3, 1.5), b"\x01\x02\x03")
        self.assertEqual([call.args for call in select_mock.call_args_list], [([9], [], [], 1.25), ([9], [], [], 0.75)])
        self.assertEqual([call.args for call in read_mock.call_args_list], [(9, 3), (9, 2)])

        with (
            patch("venus_evcharger.backend.modbus_transport.time.monotonic", side_effect=[2.0, 2.2]),
            patch("venus_evcharger.backend.modbus_transport.select.select", return_value=([], [], [])),
        ):
            with self.assertRaisesRegex(ModbusTimeoutError, "^Timed out waiting for Modbus RTU response$"):
                ModbusSerialRtuTransport._read_exact(9, 1, 0.5)

        with (
            patch("venus_evcharger.backend.modbus_transport.time.monotonic", side_effect=[2.0, 2.2]),
            patch("venus_evcharger.backend.modbus_transport.select.select", return_value=([9], [], [])),
            patch("venus_evcharger.backend.modbus_transport.os.read", return_value=b""),
        ):
            with self.assertRaisesRegex(
                ModbusTimeoutError,
                "^Modbus RTU transport closed before full response was received$",
            ):
                ModbusSerialRtuTransport._read_exact(9, 1, 0.5)

    def test_create_modbus_transport_returns_expected_transport_classes(self) -> None:
        base_kwargs = dict(unit_id=1, timeout_seconds=1.0, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        serial_settings = ModbusTransportSettings(transport_kind="serial_rtu", host=None, port=None, device="/dev/ttyS7", **base_kwargs)
        udp_settings = ModbusTransportSettings(transport_kind="udp", host="127.0.0.1", port=502, device=None, **base_kwargs)
        tcp_settings = ModbusTransportSettings(transport_kind="tcp", host="127.0.0.1", port=502, device=None, **base_kwargs)
        serial_transport = create_modbus_transport(serial_settings)
        udp_transport = create_modbus_transport(udp_settings)
        tcp_transport = create_modbus_transport(tcp_settings)
        self.assertIsInstance(serial_transport, ModbusSerialRtuTransport)
        self.assertIsInstance(udp_transport, ModbusUdpTransport)
        self.assertIsInstance(tcp_transport, ModbusTcpTransport)
        self.assertIs(serial_transport.settings, serial_settings)
        self.assertIs(udp_transport.settings, udp_settings)
        self.assertIs(tcp_transport.settings, tcp_settings)

    def test_serial_rtu_exchange_covers_remaining_error_and_recovery_paths(self) -> None:
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=1, retry_delay=0.1, owner="venus_serial_starter"))
        transport._port_owner = MagicMock()
        with patch.object(transport, "_exchange_once", side_effect=ModbusPortBusyError("busy")):
            with self.assertRaises(ModbusPortBusyError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch.object(transport, "_exchange_once", side_effect=ModbusPortOwnershipError("ownership")):
            with self.assertRaises(ModbusPortOwnershipError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch.object(transport, "_exchange_once", side_effect=ModbusResponseError("response")):
            with self.assertRaises(ModbusResponseError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch.object(transport, "_exchange_once", side_effect=OSError(errno.EIO, "io")):
            with self.assertRaises(ModbusTransportError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch("venus_evcharger.backend.modbus_transport.time.sleep") as sleep_mock:
            transport._recover_after_failure(ModbusTimeoutError("timeout"))
        transport._port_owner.recover.assert_called()
        sleep_mock.assert_called_once_with(0.1)

        no_delay_transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=1, retry_delay=0.0))
        no_delay_transport._port_owner = MagicMock()
        with patch("venus_evcharger.backend.modbus_transport.time.sleep") as no_delay_sleep:
            no_delay_transport._recover_after_failure(ModbusTimeoutError("timeout"))
        no_delay_transport._port_owner.recover.assert_called_once()
        no_delay_sleep.assert_not_called()

    def test_modbus_transport_runtime_helpers_cover_remaining_error_paths(self) -> None:
        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x83\x02"), 5)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            _expected_rtu_response_length(0x03, b"\x01\x03")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            _expected_rtu_response_length(0x07, b"\x01\x07\x02")

        tcp_settings = ModbusTransportSettings(transport_kind="tcp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        udp_settings = ModbusTransportSettings(transport_kind="udp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        tcp = ModbusTcpTransport(tcp_settings)
        udp = ModbusUdpTransport(udp_settings)

        fake_sock = MagicMock()
        fake_sock.__enter__.return_value = fake_sock
        fake_sock.__exit__.return_value = False
        with patch("venus_evcharger.backend.modbus_transport.socket.create_connection", return_value=fake_sock), patch("venus_evcharger.backend.modbus_transport._recv_exact", side_effect=[b"\x00\x01\x00\x00\x00\x01\x01", b""]):
            self.assertEqual(tcp.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0), b"")

        udp_sock = MagicMock()
        udp_sock.__enter__.return_value = udp_sock
        udp_sock.__exit__.return_value = False
        udp_sock.recvfrom.return_value = (b"\x00\x01", ("127.0.0.1", 502))
        with patch("venus_evcharger.backend.modbus_transport.socket.socket", return_value=udp_sock):
            with self.assertRaises(TimeoutError):
                udp.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0)

        transport = ModbusSerialRtuTransport(self._serial_settings())
        with patch("venus_evcharger.backend.modbus_transport.os.open", return_value=5), patch("venus_evcharger.backend.modbus_transport.os.close"), patch("venus_evcharger.backend.modbus_transport.termios.tcsetattr"), patch("venus_evcharger.backend.modbus_transport.termios.tcflush"), patch("venus_evcharger.backend.modbus_transport._configured_serial_attrs", return_value=[]), patch.object(transport, "_write_all"), patch.object(transport, "_read_exact", side_effect=[b"\x01\x03\x02", b"\x00\xa0\x00\x00"]):
            with self.assertRaises(ModbusResponseError):
                transport._exchange_once(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), 1.0)

        with patch("venus_evcharger.backend.modbus_transport.select.select", return_value=([], [], [])), patch("venus_evcharger.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0]):
            with self.assertRaises(ModbusTimeoutError):
                ModbusSerialRtuTransport._read_exact(5, 1, 1.0)

        self.assertIsInstance(
            ModbusSerialRtuTransport._normalized_serial_os_error(OSError(errno.EPERM, "nope")),
            ModbusPortBusyError,
        )

    def test_serial_os_error_normalization_maps_all_busy_errnos_and_generic_error(self) -> None:
        for error_number in (errno.EBUSY, errno.EACCES, errno.EPERM):
            with self.subTest(error_number=error_number):
                error = ModbusSerialRtuTransport._normalized_serial_os_error(OSError(error_number, "busy"))
                self.assertIsInstance(error, ModbusPortBusyError)
                self.assertIn("busy", str(error))
        generic = ModbusSerialRtuTransport._normalized_serial_os_error(OSError(errno.EIO, "io"))
        self.assertIsInstance(generic, ModbusTransportError)
        self.assertNotIsInstance(generic, ModbusPortBusyError)
        self.assertIn("io", str(generic))

    def test_modbus_transport_runtime_covers_parity_owner_and_short_reads(self) -> None:
        odd_settings = self._serial_settings()
        odd_settings = ModbusTransportSettings(**{**odd_settings.__dict__, "parity": "O", "stopbits": 2, "device": None})
        with patch("venus_evcharger.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0, 0, 0, 0, 0, 0, 0]]):
            attrs = _configured_serial_attrs(5, odd_settings)
        self.assertTrue(attrs[2])

        even_settings = ModbusTransportSettings(**{**self._serial_settings().__dict__, "parity": "E", "stopbits": 1, "device": None})
        with patch("venus_evcharger.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0, 0, 0, 0, 0, 0, 0]]):
            even_attrs = _configured_serial_attrs(5, even_settings)
        self.assertTrue(even_attrs[2])

        none_settings = ModbusTransportSettings(**{**self._serial_settings().__dict__, "parity": "N", "stopbits": 1, "device": None})
        with patch("venus_evcharger.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0, 0, 0, 0, 0, 0, 0]]):
            none_attrs = _configured_serial_attrs(5, none_settings)
        self.assertTrue(isinstance(none_attrs, list))

        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        owner._run_command = MagicMock()
        owner._owned = True
        owner.recover()
        owner._run_command.assert_called_once_with("/stop.sh")
        owner._owned = False
        owner.ensure_owned()
        self.assertTrue(owner._owned)

        owner_settings = ModbusTransportSettings(**{**self._serial_settings(owner="venus_serial_starter").__dict__, "device": None})
        self.assertIsNone(_serial_port_owner(owner_settings))

        transport = ModbusSerialRtuTransport(self._serial_settings())
        with patch("venus_evcharger.backend.modbus_transport.os.open", return_value=5), patch("venus_evcharger.backend.modbus_transport.os.close"), patch("venus_evcharger.backend.modbus_transport.termios.tcsetattr"), patch("venus_evcharger.backend.modbus_transport.termios.tcflush"), patch("venus_evcharger.backend.modbus_transport._configured_serial_attrs", return_value=[]), patch.object(transport, "_write_all"), patch.object(transport, "_read_exact", side_effect=[b"\x01\x03\x02", b"\x00"]):
            with self.assertRaises(ModbusTimeoutError):
                transport._exchange_once(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), 1.0)

        with patch("venus_evcharger.backend.modbus_transport.select.select", return_value=([5], [], [])), patch("venus_evcharger.backend.modbus_transport.os.read", return_value=b""), patch("venus_evcharger.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0]):
            with self.assertRaises(ModbusTimeoutError):
                ModbusSerialRtuTransport._read_exact(5, 1, 1.0)
