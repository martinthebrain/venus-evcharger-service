# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class SwitchBackendTestCaseBase(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            host="192.0.2.11",
            username="",
            password="",
            use_digest_auth=False,
            shelly_request_timeout_seconds=2.0,
            pm_component="Switch",
            pm_id=0,
            phase="L1",
            max_current=16.0,
            _last_voltage=230.0,
        )

    @staticmethod
    def _write_switch_config(directory: str) -> str:
        path = Path(directory) / "switch.ini"
        path.write_text(
            "[Adapter]\nType=shelly_switch\nHost=192.0.2.11\nComponent=Switch\nId=0\n"
            "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n"
            "[PhaseMap]\nP1=0\nP1_P2=0,1\nP1_P2_P3=0,1,2\n",
            encoding="utf-8",
        )
        return str(path)

    @staticmethod
    def _write_switch_group_config(directory: str) -> str:
        p1_path = Path(directory) / "phase1-switch.ini"
        p2_path = Path(directory) / "phase2-switch.ini"
        p3_path = Path(directory) / "phase3-switch.ini"
        p1_path.write_text(
            "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
            "[StateRequest]\nMethod=GET\nUrl=/state\n"
            "[StateResponse]\nEnabledPath=enabled\n"
            "[CommandRequest]\nMethod=POST\nUrl=/control\n"
            'JsonTemplate={"enabled": $enabled_json}\n',
            encoding="utf-8",
        )
        p2_path.write_text(
            "[Adapter]\nType=shelly_switch\nHost=192.0.2.22\nComponent=Switch\nId=0\n",
            encoding="utf-8",
        )
        p3_path.write_text(
            "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
            "[StateRequest]\nMethod=GET\nUrl=/state\n"
            "[StateResponse]\nEnabledPath=enabled\n"
            "[CommandRequest]\nMethod=POST\nUrl=/control\n"
            'JsonTemplate={"enabled": $enabled_json}\n',
            encoding="utf-8",
        )
        path = Path(directory) / "switch-group.ini"
        path.write_text(
            "[Adapter]\nType=switch_group\n"
            "[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\nP3=phase3-switch.ini\n",
            encoding="utf-8",
        )
        return str(path)


__all__ = ["SwitchBackendTestCaseBase", "_FakeResponse", "MagicMock", "Path", "tempfile"]
