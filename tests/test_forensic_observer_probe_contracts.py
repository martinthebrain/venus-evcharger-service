# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenario contracts for optional backend-specific forensic probes."""

from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.config_diagnostics import BackendSelectionView
from venus_evcharger.ops import forensic_observer_probe as probes


def _config(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(f"[DEFAULT]\n{text}")
    return parser


def _selection(
    *,
    meter_type: str = "shelly_meter",
    switch_type: str = "shelly_contactor_switch",
    charger_type: str | None = None,
    meter_path: Path | None = None,
    switch_path: Path | None = None,
    charger_path: Path | None = None,
) -> BackendSelectionView:
    return {
        "mode": "split",
        "meter_type": meter_type,
        "switch_type": switch_type,
        "charger_type": charger_type,
        "meter_config_path": meter_path,
        "switch_config_path": switch_path,
        "charger_config_path": charger_path,
    }


class ForensicObserverProbeContractTests(unittest.TestCase):
    def test_probe_result_and_noop_probe_payloads_are_exact(self) -> None:
        result = probes.BackendProbeResult(
            status="error",
            probe_type="probe",
            role="meter",
            backend_type="backend",
            reason_code="reason",
            payload="payload",
        )
        self.assertEqual(
            result.to_payload(),
            {
                "status": "error",
                "probe_type": "probe",
                "role": "meter",
                "backend_type": "backend",
                "reason_code": "reason",
                "payload": "payload",
            },
        )
        self.assertEqual(
            probes.DisabledBackendProbe().probe().to_payload(),
            {
                "status": "disabled",
                "probe_type": "none",
                "role": "",
                "backend_type": "",
                "reason_code": "direct-probe-disabled",
                "payload": "",
            },
        )
        self.assertEqual(
            probes.RejectedBackendProbe("probe", "switch", "backend", "reason")
            .probe()
            .to_payload(),
            {
                "status": "skipped",
                "probe_type": "probe",
                "role": "switch",
                "backend_type": "backend",
                "reason_code": "reason",
                "payload": "",
            },
        )

    def test_probe_roles_and_backend_selection_are_total(self) -> None:
        selection = _selection(
            charger_type="shelly_charger",
            meter_path=Path("meter.ini"),
            switch_path=Path("switch.ini"),
            charger_path=Path("charger.ini"),
        )
        self.assertEqual(probes._probe_role(" METER "), "meter")
        self.assertEqual(probes._probe_role("switch"), "switch")
        self.assertEqual(probes._probe_role("CHARGER"), "charger")
        self.assertIsNone(probes._probe_role("other"))
        self.assertEqual(
            probes._selected_backend(selection, "meter"),
            ("shelly_meter", Path("meter.ini")),
        )
        self.assertEqual(
            probes._selected_backend(selection, "switch"),
            ("shelly_contactor_switch", Path("switch.ini")),
        )
        self.assertEqual(
            probes._selected_backend(selection, "charger"),
            ("shelly_charger", Path("charger.ini")),
        )
        selection["charger_type"] = None
        self.assertEqual(
            probes._selected_backend(selection, "charger"),
            ("", Path("charger.ini")),
        )

    def test_venus_templates_document_disabled_backend_probe_defaults(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "deploy/venus/config.venus_evcharger.default.ini",
            "deploy/venus/config.venus_evcharger.ini",
        ):
            with self.subTest(template=relative_path):
                parser = configparser.ConfigParser()
                self.assertEqual(parser.read(root / relative_path), [str(root / relative_path)])
                defaults = parser["DEFAULT"]
                self.assertEqual(defaults["ForensicBackendProbe"], "disabled")
                self.assertEqual(defaults["ForensicBackendProbeRole"], "switch")

    def test_direct_probe_is_disabled_by_default(self) -> None:
        with patch.object(probes.urllib.request, "urlopen") as urlopen:
            result = probes.configured_backend_probe(
                _config("Host=192.0.2.1\n"),
                _selection(),
                config_path="/tmp/config.ini",
            ).probe()
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.reason_code, "direct-probe-disabled")
        urlopen.assert_not_called()

    def test_disabled_probe_aliases_never_build_an_active_probe(self) -> None:
        for value in ("", "disabled", "none", "off", " NONE ", " OFF "):
            with self.subTest(value=value):
                configured = _config(f"ForensicBackendProbe={value}\n")
                with patch.object(probes, "_configured_active_probe") as active:
                    result = probes.configured_backend_probe(
                        configured,
                        _selection(),
                        config_path="/tmp/config.ini",
                    )
                self.assertEqual(result, probes.DisabledBackendProbe())
                active.assert_not_called()

    def test_missing_probe_and_role_have_explicit_safe_defaults(self) -> None:
        config = _config("Host=192.0.2.30\n")
        selection = _selection()
        with patch.object(probes, "_configured_active_probe") as active:
            self.assertEqual(
                probes.configured_backend_probe(
                    config,
                    selection,
                    config_path="/tmp/main.ini",
                ),
                probes.DisabledBackendProbe(),
            )
        active.assert_not_called()
        self.assertEqual(probes._configured_probe_role(config["DEFAULT"]), "switch")

    def test_active_probe_configuration_is_normalized_and_forwarded_exactly(self) -> None:
        config = _config("ForensicBackendProbe= Shelly-RPC \n")
        expected = probes.DisabledBackendProbe("sentinel")
        selection = _selection()
        with patch.object(
            probes,
            "_configured_active_probe",
            return_value=expected,
        ) as active:
            actual = probes.configured_backend_probe(
                config,
                selection,
                config_path="/tmp/main.ini",
            )
        self.assertIs(actual, expected)
        active.assert_called_once_with(
            config,
            selection,
            probe_type="shelly-rpc",
            config_path="/tmp/main.ini",
        )

    def test_tuya_backend_never_triggers_a_shelly_request(self) -> None:
        config = _config(
            "ForensicBackendProbe=shelly-rpc\n"
            "ForensicBackendProbeRole=switch\n"
            "Host=192.0.2.1\n"
        )
        with patch.object(probes.urllib.request, "urlopen") as urlopen:
            result = probes.configured_backend_probe(
                config,
                _selection(switch_type="tuya_switch"),
                config_path="/tmp/config.ini",
            ).probe()
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.backend_type, "tuya_switch")
        self.assertEqual(result.reason_code, "backend-type-mismatch")
        urlopen.assert_not_called()

    def test_explicit_shelly_probe_uses_validated_inline_backend(self) -> None:
        config = _config(
            "ForensicBackendProbe=shelly-rpc\n"
            "ForensicBackendProbeRole=switch\n"
            "Host=192.0.2.5\n"
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"\xffstatus"
        with patch.object(
            probes.urllib.request,
            "urlopen",
            return_value=response,
        ) as urlopen:
            result = probes.configured_backend_probe(
                config,
                _selection(),
                config_path="/tmp/config.ini",
            ).probe()
        urlopen.assert_called_once_with(
            "http://192.0.2.5/rpc/Shelly.GetStatus",
            timeout=2.0,
        )
        response.__enter__.return_value.read.assert_called_once_with(65536)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.payload, "\ufffdstatus")

    def test_split_shelly_probe_reads_role_specific_adapter_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "switch.ini"
            backend.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.0.2.9\n",
                encoding="utf-8",
            )
            config = _config(
                "ForensicBackendProbe=shelly-rpc\n"
                "ForensicBackendProbeRole=switch\n"
                "Host=wrong.example\n"
            )
            response = MagicMock()
            response.__enter__.return_value.read.return_value = b"{}"
            with patch.object(
                probes.urllib.request,
                "urlopen",
                return_value=response,
            ) as urlopen:
                probes.configured_backend_probe(
                    config,
                    _selection(switch_path=Path("switch.ini")),
                    config_path=str(root / "config.ini"),
                ).probe()
        urlopen.assert_called_once_with(
            "http://192.0.2.9/rpc/Shelly.GetStatus",
            timeout=2.0,
        )

    def test_backend_config_resolution_is_bounded_for_absolute_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            absolute = root / "meter.ini"
            absolute.write_text("[DEFAULT]\nHost=192.0.2.10\n", encoding="utf-8")
            config = _config(
                "ForensicBackendProbe=shelly-rpc\n"
                "ForensicBackendProbeRole=meter\n"
            )
            configured = probes.configured_backend_probe(
                config,
                _selection(meter_path=absolute),
                config_path=str(root / "config.ini"),
            )
            missing = probes.configured_backend_probe(
                config,
                _selection(meter_path=Path("missing.ini")),
                config_path=str(root / "config.ini"),
            )
        self.assertIsInstance(configured, probes.ShellyRpcBackendProbe)
        if not isinstance(configured, probes.ShellyRpcBackendProbe):
            self.fail("expected a configured Shelly probe")
        self.assertEqual(configured.host, "192.0.2.10")
        self.assertEqual(missing.probe().reason_code, "backend-host-missing")

    def test_misconfiguration_and_network_errors_are_bounded_results(self) -> None:
        invalid_role = probes.configured_backend_probe(
            _config("ForensicBackendProbe=shelly-rpc\nForensicBackendProbeRole=other\n"),
            _selection(),
            config_path="/tmp/config.ini",
        ).probe()
        self.assertEqual(invalid_role.reason_code, "invalid-probe-role")

        unsupported = probes.configured_backend_probe(
            _config("ForensicBackendProbe=tuya-cloud\n"),
            _selection(),
            config_path="/tmp/config.ini",
        ).probe()
        self.assertEqual(unsupported.reason_code, "unsupported-probe-type")

        missing_host = probes.configured_backend_probe(
            _config("ForensicBackendProbe=shelly-rpc\n"),
            _selection(),
            config_path="/tmp/config.ini",
        ).probe()
        self.assertEqual(missing_host.reason_code, "backend-host-missing")

        probe = probes.ShellyRpcBackendProbe(
            "192.0.2.5",
            "switch",
            "shelly_switch",
        )
        with patch.object(
            probes.urllib.request,
            "urlopen",
            side_effect=TimeoutError("late"),
        ):
            failed = probe.probe()
        self.assertEqual(
            failed.to_payload(),
            {
                "status": "error",
                "probe_type": "shelly-rpc",
                "role": "switch",
                "backend_type": "shelly_switch",
                "reason_code": "backend-probe-failed",
                "payload": "late",
            },
        )

    def test_active_probe_rejections_and_success_are_exact(self) -> None:
        cases = (
            (
                _config("ForensicBackendProbeRole=other\n"),
                _selection(),
                "shelly-rpc",
                probes.RejectedBackendProbe(
                    "shelly-rpc",
                    "",
                    "",
                    "invalid-probe-role",
                ),
            ),
            (
                _config("ForensicBackendProbeRole=meter\n"),
                _selection(),
                "other",
                probes.RejectedBackendProbe(
                    "other",
                    "meter",
                    "shelly_meter",
                    "unsupported-probe-type",
                ),
            ),
            (
                _config("ForensicBackendProbeRole=charger\n"),
                _selection(charger_type="tuya_charger"),
                "shelly-rpc",
                probes.RejectedBackendProbe(
                    "shelly-rpc",
                    "charger",
                    "tuya_charger",
                    "backend-type-mismatch",
                ),
            ),
            (
                _config("ForensicBackendProbeRole=switch\n"),
                _selection(),
                "shelly-rpc",
                probes.RejectedBackendProbe(
                    "shelly-rpc",
                    "switch",
                    "shelly_contactor_switch",
                    "backend-host-missing",
                ),
            ),
        )
        for configured, selection, probe_type, expected in cases:
            with self.subTest(reason=expected.reason_code):
                self.assertEqual(
                    probes._configured_active_probe(
                        configured,
                        selection,
                        probe_type=probe_type,
                        config_path="/tmp/main.ini",
                    ),
                    expected,
                )

        configured = _config(
            "ForensicBackendProbeRole=meter\n"
            "Host= 192.0.2.20 \n"
        )
        self.assertEqual(
            probes._configured_active_probe(
                configured,
                _selection(),
                probe_type="shelly-rpc",
                config_path="/tmp/main.ini",
            ),
            probes.ShellyRpcBackendProbe(
                "192.0.2.20",
                "meter",
                "shelly_meter",
                timeout_seconds=2.0,
            ),
        )

    def test_backend_host_prefers_adapter_section_and_trims_inline_host(self) -> None:
        self.assertEqual(
            probes._backend_host(
                _config("Host= inline.example \n"),
                None,
                config_path="/tmp/main.ini",
            ),
            "inline.example",
        )
        backend = configparser.ConfigParser()
        backend.read_string(
            "[DEFAULT]\nHost=default.example\n"
            "[Adapter]\nHost= adapter.example \n"
        )
        with patch.object(
            probes,
            "_read_backend_config",
            return_value=backend,
        ) as read_backend:
            self.assertEqual(
                probes._backend_host(
                    _config("Host=inline.example\n"),
                    Path("switch.ini"),
                    config_path="/tmp/main.ini",
                ),
                "adapter.example",
            )
        read_backend.assert_called_once_with(Path("switch.ini"), "/tmp/main.ini")


if __name__ == "__main__":
    unittest.main()
