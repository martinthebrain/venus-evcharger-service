# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
import json
from configparser import ConfigParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot, read_energy_source_step
from venus_evcharger.energy import connectors as energy_connectors


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self._encoded = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Length": str(len(self._encoded))}
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload

    def iter_content(self, chunk_size: int) -> tuple[bytes, ...]:
        return tuple(
            self._encoded[offset : offset + chunk_size]
            for offset in range(0, len(self._encoded), chunk_size)
        )

    def close(self) -> None:
        self.closed = True


class _FakeModbusTransport:
    def exchange(self, request: object, *, timeout_seconds: float) -> bytes:
        _ = timeout_seconds
        function_code = getattr(request, "function_code")
        payload = getattr(request, "payload")
        address = int.from_bytes(payload[:2], "big")
        count = int.from_bytes(payload[2:4], "big")
        if function_code != 0x03:
            raise AssertionError("unexpected Modbus function")
        values = {
            10: (645,),
            20: (12000,),
            30: (0xF830,),
            40: (3200,),
            50: (4,),
            60: (900,),
            70: (1400,),
            80: (0xFFFE, 0xD4F0,),
        }
        registers = values[address]
        if len(registers) != count:
            raise AssertionError("unexpected register count")
        register_bytes = b"".join(int(register).to_bytes(2, "big") for register in registers)
        return bytes((0x03, len(register_bytes))) + register_bytes


class _EnergyConnectorsTestBase(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "external-energy.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    @staticmethod
    def _read_complete(
        owner: object,
        source: EnergySourceDefinition,
        observed_at: float,
    ) -> EnergySourceSnapshot:
        for _step_index in range(32):
            step = read_energy_source_step(owner, source, observed_at)
            if step.snapshot is not None:
                return step.snapshot
        raise AssertionError("energy connector did not complete within 32 steps")


__all__ = [name for name in globals() if not name.startswith("__")]
