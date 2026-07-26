# SPDX-License-Identifier: GPL-3.0-or-later
"""Hard allocation and lifecycle contracts for external energy I/O."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, call, patch

from venus_evcharger.backend.http_json_transport import decoded_json_response
from venus_evcharger.energy.bounded_subprocess import (
    _SpawnedCommand,
    _collect_output,
    _read_ready_pipe,
    _remaining_seconds,
    _spawn_command,
    _terminate_process,
    _wait_for_process,
    run_bounded_command,
)
from venus_evcharger.energy.http_session import ConnectorHttpSession


class _StreamingResponse:
    def __init__(
        self,
        chunks: tuple[object, ...],
        *,
        content_length: str | None = None,
    ) -> None:
        self._chunks = chunks
        self.headers = (
            {} if content_length is None else {"Content-Length": content_length}
        )
        self.closed = False
        self.chunk_size = 0

    def iter_content(self, chunk_size: int) -> tuple[object, ...]:
        self.chunk_size = chunk_size
        return self._chunks

    def close(self) -> None:
        self.closed = True


class _JsonResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def json(self) -> object:
        return self.payload


class _ClosableSession:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _EmptySelector:
    def __init__(self) -> None:
        self.registered: list[tuple[int, int]] = []
        self.select_timeouts: list[float | None] = []
        self.closed = False
        self._active = True

    def register(self, file_descriptor: int, events: int) -> None:
        self.registered.append((file_descriptor, events))

    def get_map(self) -> dict[int, object]:
        if not self._active:
            return {}
        return {file_descriptor: object() for file_descriptor, _events in self.registered}

    def select(self, timeout: float | None) -> list[object]:
        self.select_timeouts.append(timeout)
        self._active = False
        return []

    def close(self) -> None:
        self.closed = True


class ExternalEnergyIoBoundaryContracts(unittest.TestCase):
    def test_http_json_is_streamed_and_closed_within_exact_limit(self) -> None:
        response = _StreamingResponse(
            (b'{"value":', b" 7}"),
            content_length="12",
        )

        self.assertEqual(decoded_json_response(response, 12), {"value": 7})
        self.assertEqual(response.chunk_size, 16384)
        self.assertTrue(response.closed)

    def test_http_json_boundary_values_and_utf8_decode_are_exact(self) -> None:
        exact_stream = _StreamingResponse((b"12", b"34"), content_length="4")
        self.assertEqual(decoded_json_response(exact_stream, 4), 1234)
        self.assertTrue(exact_stream.closed)

        empty = _StreamingResponse((), content_length="0")
        self.assertEqual(decoded_json_response(empty, 1), {})
        self.assertTrue(empty.closed)

        utf8 = _StreamingResponse((b'"\xc3\xa4"',), content_length="4")
        self.assertEqual(decoded_json_response(utf8, 4), "\u00e4")
        self.assertTrue(utf8.closed)

        for limit in (0, -1):
            response = _StreamingResponse((b"{}",))
            with self.subTest(limit=limit), self.assertRaises(ValueError) as invalid_limit:
                decoded_json_response(response, limit)
            self.assertEqual(str(invalid_limit.exception), "HTTP JSON response limit must be positive")
            self.assertFalse(response.closed)

    def test_http_json_closes_stream_when_decode_fails(self) -> None:
        response = _StreamingResponse((b"{invalid-json",))

        with self.assertRaises(ValueError):
            decoded_json_response(response, 64)

        self.assertTrue(response.closed)

    def test_http_json_rejects_declared_and_streamed_oversize_before_decode(self) -> None:
        declared = _StreamingResponse((b"{}",), content_length="3")
        with self.assertRaisesRegex(ValueError, "exceeds 2 bytes"):
            decoded_json_response(declared, 2)
        self.assertTrue(declared.closed)

        streamed = _StreamingResponse((b"12", b"3"))
        with self.assertRaisesRegex(ValueError, "exceeds 2 bytes"):
            decoded_json_response(streamed, 2)
        self.assertTrue(streamed.closed)

    def test_http_json_rejects_invalid_length_and_non_byte_chunks(self) -> None:
        for length, message in (
            ("invalid", "HTTP response has an invalid Content-Length"),
            ("-1", "HTTP response has a negative Content-Length"),
        ):
            response = _StreamingResponse((b"{}",), content_length=length)
            with self.subTest(length=length), self.assertRaises(ValueError) as invalid_length:
                decoded_json_response(response, 10)
            self.assertEqual(str(invalid_length.exception), message)
            self.assertTrue(response.closed)

        response = _StreamingResponse(("not-bytes",))
        with self.assertRaises(TypeError) as non_bytes:
            decoded_json_response(response, 10)
        self.assertEqual(
            str(non_bytes.exception),
            "HTTP response stream yielded non-bytes content",
        )
        self.assertTrue(response.closed)

    def test_http_json_unbounded_compatibility_and_contract_errors_are_explicit(self) -> None:
        payload = {"value": 3}
        self.assertIs(decoded_json_response(_JsonResponse(payload), None), payload)
        with self.assertRaisesRegex(ValueError, "limit must be positive"):
            decoded_json_response(_StreamingResponse((b"{}",)), 0)
        with self.assertRaises(TypeError) as no_stream:
            decoded_json_response(_JsonResponse(payload), 10)
        self.assertEqual(
            str(no_stream.exception),
            "Bounded HTTP JSON response does not support streaming",
        )
        with self.assertRaises(TypeError) as no_json:
            decoded_json_response(object(), None)
        self.assertEqual(
            str(no_json.exception),
            "HTTP response does not provide JSON decoding",
        )
        self.assertEqual(decoded_json_response(_StreamingResponse(()), 10), {})

    def test_command_output_is_bounded_and_preserves_both_streams(self) -> None:
        result = run_bounded_command(
            (
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('out'); sys.stderr.write('err')",
            ),
            timeout_seconds=1.0,
            stdout_limit=3,
            stderr_limit=3,
        )

        self.assertEqual((result.stdout, result.stderr), ("out", "err"))

    def test_command_rejects_oversize_timeout_failure_and_invalid_utf8(self) -> None:
        with self.assertRaisesRegex(ValueError, "stdout exceeds 4 bytes"):
            run_bounded_command(
                (sys.executable, "-c", "print('12345', end='')"),
                timeout_seconds=1.0,
                stdout_limit=4,
                stderr_limit=4,
            )
        with self.assertRaisesRegex(ValueError, "stderr exceeds 4 bytes"):
            run_bounded_command(
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stderr.write('12345')",
                ),
                timeout_seconds=1.0,
                stdout_limit=4,
                stderr_limit=4,
            )
        with self.assertRaises(subprocess.TimeoutExpired):
            run_bounded_command(
                (sys.executable, "-c", "while True: pass"),
                timeout_seconds=0.02,
                stdout_limit=10,
                stderr_limit=10,
            )
        with self.assertRaises(subprocess.CalledProcessError) as failed:
            run_bounded_command(
                (
                    sys.executable,
                    "-c",
                    "import sys; print('bad'); print('reason', file=sys.stderr); sys.exit(7)",
                ),
                timeout_seconds=1.0,
                stdout_limit=16,
                stderr_limit=16,
            )
        self.assertEqual(failed.exception.returncode, 7)
        self.assertEqual(failed.exception.cmd, (sys.executable, "-c", "import sys; print('bad'); print('reason', file=sys.stderr); sys.exit(7)"))
        self.assertEqual(failed.exception.output, "bad\n")
        self.assertEqual(failed.exception.stderr, "reason\n")
        with self.assertRaisesRegex(ValueError, "stdout is not valid UTF-8"):
            run_bounded_command(
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(bytes((255,)))",
                ),
                timeout_seconds=1.0,
                stdout_limit=1,
                stderr_limit=1,
            )
        with self.assertRaises(ValueError) as invalid_stderr:
            run_bounded_command(
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stderr.buffer.write(bytes((255,)))",
                ),
                timeout_seconds=1.0,
                stdout_limit=1,
                stderr_limit=1,
            )
        self.assertEqual(str(invalid_stderr.exception), "Command stderr is not valid UTF-8")

    def test_command_argument_and_spawn_contracts_fail_before_execution(self) -> None:
        invalid = (
            ((), 1.0, 1, 1, "must not be empty"),
            (("",), 1.0, 1, 1, "must not be empty"),
            (("true",), 0.0, 1, 1, "timeout must be positive"),
            (("true",), 1.0, 0, 1, "limits must be positive"),
            (("true",), 1.0, 1, 0, "limits must be positive"),
        )
        for command, timeout, stdout_limit, stderr_limit, message in invalid:
            with self.subTest(command=command), self.assertRaises(ValueError) as invalid_argument:
                run_bounded_command(
                    command,
                    timeout_seconds=timeout,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                )
            self.assertIn(message, str(invalid_argument.exception))
        exact_errors = (
            ((), 1.0, 1, 1, "Command must not be empty"),
            (("",), 1.0, 1, 1, "Command must not be empty"),
            (("true",), 0.0, 1, 1, "Command timeout must be positive"),
            (("true",), 1.0, 0, 1, "Command output limits must be positive"),
            (("true",), 1.0, 1, 0, "Command output limits must be positive"),
        )
        for command, timeout, stdout_limit, stderr_limit, message in exact_errors:
            with self.subTest(exact_message=message), self.assertRaises(ValueError) as invalid_argument:
                run_bounded_command(
                    command,
                    timeout_seconds=timeout,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                )
            self.assertEqual(str(invalid_argument.exception), message)
        with self.assertRaises(FileNotFoundError):
            run_bounded_command(
                ("definitely-not-an-installed-energy-helper",),
                timeout_seconds=1.0,
                stdout_limit=1,
                stderr_limit=1,
            )

    def test_process_completion_cleanup_and_expired_deadline_contracts(self) -> None:
        process = _SpawnedCommand(42, ("helper",), 3, 4)
        with patch(
            "venus_evcharger.energy.bounded_subprocess.os.waitpid",
            side_effect=((0, 0), (42, 7 << 8)),
        ) as waitpid, patch(
            "venus_evcharger.energy.bounded_subprocess.time.sleep",
        ) as sleep:
            self.assertEqual(_wait_for_process(process, float("inf")), 7)
        self.assertEqual(waitpid.call_count, 2)
        sleep.assert_called_once_with(0.01)

        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.os.kill",
                side_effect=ProcessLookupError,
            ) as kill,
            patch(
                "venus_evcharger.energy.bounded_subprocess.os.waitpid",
                side_effect=ChildProcessError,
            ) as waitpid,
        ):
            _terminate_process(process)
        kill.assert_called_once_with(process.pid, 9)
        waitpid.assert_called_once_with(process.pid, 0)

        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.time.monotonic",
                return_value=2.0,
            ),
            self.assertRaises(subprocess.TimeoutExpired) as expired,
        ):
            _remaining_seconds(2.0, process.command)
        self.assertEqual(expired.exception.cmd, process.command)
        self.assertEqual(expired.exception.timeout, 0.0)

        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.os.waitpid",
                return_value=(0, 0),
            ),
            patch(
                "venus_evcharger.energy.bounded_subprocess.time.monotonic",
                return_value=2.0,
            ),
            self.assertRaises(subprocess.TimeoutExpired) as wait_expired,
        ):
            _wait_for_process(process, 2.0)
        self.assertEqual(wait_expired.exception.cmd, process.command)

    def test_spawn_and_collection_preserve_command_deadline_and_pipe_contracts(self) -> None:
        command = ("helper", "--value")
        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.os.pipe",
                side_effect=((10, 11), (20, 21)),
            ),
            patch(
                "venus_evcharger.energy.bounded_subprocess.os.posix_spawnp",
                return_value=42,
            ) as spawn,
            patch("venus_evcharger.energy.bounded_subprocess.os.close") as close,
        ):
            process = _spawn_command(command)
        self.assertEqual(process, _SpawnedCommand(42, command, 10, 20))
        self.assertIs(spawn.call_args.args[2], os.environ)
        self.assertEqual(spawn.call_args.args[:2], ("helper", command))
        self.assertEqual(close.call_args_list, [call(11), call(21)])

        selector = _EmptySelector()
        process = _SpawnedCommand(42, command, 10, 20)
        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.selectors.DefaultSelector",
                return_value=selector,
            ),
            patch(
                "venus_evcharger.energy.bounded_subprocess.time.monotonic",
                return_value=1.0,
            ),
            patch("venus_evcharger.energy.bounded_subprocess.os.close"),
            self.assertRaises(subprocess.TimeoutExpired) as no_events,
        ):
            _collect_output(
                process,
                deadline=2.0,
                stdout_limit=3,
                stderr_limit=4,
            )
        self.assertEqual(selector.select_timeouts, [1.0])
        self.assertEqual(no_events.exception.cmd, command)
        self.assertEqual(no_events.exception.timeout, 0.0)
        self.assertTrue(selector.closed)

        expired_selector = _EmptySelector()
        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.selectors.DefaultSelector",
                return_value=expired_selector,
            ),
            patch(
                "venus_evcharger.energy.bounded_subprocess.time.monotonic",
                return_value=2.0,
            ),
            patch("venus_evcharger.energy.bounded_subprocess.os.close"),
            self.assertRaises(subprocess.TimeoutExpired) as expired,
        ):
            _collect_output(
                process,
                deadline=2.0,
                stdout_limit=3,
                stderr_limit=4,
            )
        self.assertEqual(expired.exception.cmd, command)

    def test_pipe_reader_enforces_exact_chunk_and_limit_boundaries(self) -> None:
        selector = MagicMock()
        buffers = {3: bytearray(b"ab")}
        labels = {3: "stdout"}
        with patch(
            "venus_evcharger.energy.bounded_subprocess.os.read",
            return_value=b"x",
        ) as read:
            _read_ready_pipe(selector, 3, buffers, {3: 20_000}, labels)
        read.assert_called_once_with(3, 16_384)

        buffers = {3: bytearray(b"ab")}
        with patch(
            "venus_evcharger.energy.bounded_subprocess.os.read",
            return_value=b"x",
        ) as read:
            _read_ready_pipe(selector, 3, buffers, {3: 4}, labels)
        read.assert_called_once_with(3, 3)

    def test_owned_session_is_reused_closed_once_and_injected_session_is_not_owned(
        self,
    ) -> None:
        owned = _ClosableSession()
        with patch(
            "venus_evcharger.energy.http_session.requests.Session",
            return_value=owned,
        ) as session_type:
            owner = ConnectorHttpSession(True, None)
        session_type.assert_called_once_with()
        self.assertIs(owner.session, owned)
        owner.close()
        owner.close()
        self.assertEqual(owned.close_calls, 1)
        self.assertIsNone(owner.session)

        injected = MagicMock()
        external = ConnectorHttpSession(True, injected)
        self.assertIs(external.session, injected)
        external.close()
        injected.close.assert_not_called()

        disabled = ConnectorHttpSession(False, None)
        self.assertIsNone(disabled.session)
        disabled.close()

    def test_owned_session_without_close_contract_fails_loudly(self) -> None:
        with patch(
            "venus_evcharger.energy.http_session.requests.Session",
            return_value=object(),
        ):
            owner = ConnectorHttpSession(True, None)
        with self.assertRaises(TypeError) as not_closable:
            owner.close()
        self.assertEqual(
            str(not_closable.exception),
            "Owned connector HTTP session cannot be closed",
        )


if __name__ == "__main__":
    unittest.main()
