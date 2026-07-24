# SPDX-License-Identifier: GPL-3.0-or-later
"""Hard allocation and lifecycle contracts for external energy I/O."""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.http_json_transport import decoded_json_response
from venus_evcharger.energy.bounded_subprocess import (
    _SpawnedCommand,
    _remaining_seconds,
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


class ExternalEnergyIoBoundaryContracts(unittest.TestCase):
    def test_http_json_is_streamed_and_closed_within_exact_limit(self) -> None:
        response = _StreamingResponse(
            (b'{"value":', b" 7}"),
            content_length="12",
        )

        self.assertEqual(decoded_json_response(response, 12), {"value": 7})
        self.assertEqual(response.chunk_size, 16384)
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
            ("invalid", "invalid Content-Length"),
            ("-1", "negative Content-Length"),
        ):
            response = _StreamingResponse((b"{}",), content_length=length)
            with self.subTest(length=length), self.assertRaisesRegex(
                ValueError,
                message,
            ):
                decoded_json_response(response, 10)
            self.assertTrue(response.closed)

        response = _StreamingResponse(("not-bytes",))
        with self.assertRaisesRegex(TypeError, "non-bytes"):
            decoded_json_response(response, 10)
        self.assertTrue(response.closed)

    def test_http_json_unbounded_compatibility_and_contract_errors_are_explicit(self) -> None:
        payload = {"value": 3}
        self.assertIs(decoded_json_response(_JsonResponse(payload), None), payload)
        with self.assertRaisesRegex(ValueError, "limit must be positive"):
            decoded_json_response(_StreamingResponse((b"{}",)), 0)
        with self.assertRaisesRegex(TypeError, "does not support streaming"):
            decoded_json_response(_JsonResponse(payload), 10)
        with self.assertRaisesRegex(TypeError, "does not provide JSON decoding"):
            decoded_json_response(object(), None)
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

    def test_command_argument_and_spawn_contracts_fail_before_execution(self) -> None:
        invalid = (
            ((), 1.0, 1, 1, "must not be empty"),
            (("",), 1.0, 1, 1, "must not be empty"),
            (("true",), 0.0, 1, 1, "timeout must be positive"),
            (("true",), 1.0, 0, 1, "limits must be positive"),
            (("true",), 1.0, 1, 0, "limits must be positive"),
        )
        for command, timeout, stdout_limit, stderr_limit, message in invalid:
            with self.subTest(command=command), self.assertRaisesRegex(
                ValueError,
                message,
            ):
                run_bounded_command(
                    command,
                    timeout_seconds=timeout,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                )
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
        ) as waitpid:
            self.assertEqual(_wait_for_process(process, float("inf")), 7)
        self.assertEqual(waitpid.call_count, 2)

        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.os.kill",
                side_effect=ProcessLookupError,
            ),
            patch(
                "venus_evcharger.energy.bounded_subprocess.os.waitpid",
                side_effect=ChildProcessError,
            ),
        ):
            _terminate_process(process)

        with (
            patch(
                "venus_evcharger.energy.bounded_subprocess.time.monotonic",
                return_value=2.0,
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            _remaining_seconds(2.0, process.command)

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
        with self.assertRaisesRegex(TypeError, "cannot be closed"):
            owner.close()


if __name__ == "__main__":
    unittest.main()
