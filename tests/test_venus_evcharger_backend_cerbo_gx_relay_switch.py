# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the semantic GX relay backend boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from venus_evcharger.backend.cerbo_gx_relay_switch import (
    CerboGxRelaySwitchBackend,
    load_cerbo_gx_relay_switch_settings,
)
from venus_evcharger.backend.registry import create_switch_backend
from venus_evcharger.ports.gateway_operations import (
    EssSetpointIntent,
    GatewayOperationReceipt,
    GxRelaySetRequest,
)


class _GatewayOperations:
    def __init__(self, relay_state: int | None = None) -> None:
        self.relay_state = relay_state
        self.accepted = True
        self.read_calls: list[tuple[int, float]] = []
        self.set_calls: list[GxRelaySetRequest] = []

    def read_gx_relay_state(self, relay_index: int, *, max_age_seconds: float) -> int | None:
        self.read_calls.append((relay_index, max_age_seconds))
        return self.relay_state

    def set_gx_relay_enabled(
        self,
        request: GxRelaySetRequest,
    ) -> GatewayOperationReceipt:
        self.set_calls.append(request)
        return GatewayOperationReceipt(accepted=self.accepted, command_id="relay" if self.accepted else "")

    def set_ess_grid_setpoint(
        self,
        watts: float,
        *,
        intent: EssSetpointIntent,
    ) -> GatewayOperationReceipt:
        del watts, intent
        return GatewayOperationReceipt(accepted=True)


class TestCerboGxRelaySwitchBackend(unittest.TestCase):
    def _config(self, text: str) -> str:
        temp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        with temp:
            temp.write(text)
        self.addCleanup(Path(temp.name).unlink, missing_ok=True)
        return temp.name

    @staticmethod
    def _service(
        gateway: _GatewayOperations,
        *,
        requested_phase_selection: object = "P1",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            gateway_operations=gateway,
            requested_phase_selection=requested_phase_selection,
        )

    def test_default_settings_and_contact_mode_aliases(self) -> None:
        defaults = load_cerbo_gx_relay_switch_settings("")
        self.assertEqual(defaults.relay_index, 0)
        self.assertEqual(defaults.contact_mode, "NO")
        self.assertTrue(defaults.ensure_manual_function)
        self.assertEqual(defaults.verify_settle_seconds, 0.1)
        self.assertEqual(defaults.verify_retry_seconds, 0.2)
        self.assertEqual(defaults.supported_phase_selections, ("P1",))
        self.assertFalse(defaults.requires_charge_pause_for_phase_change)

        for alias in ("nc", "Normally_Closed", "normally-closed"):
            settings = load_cerbo_gx_relay_switch_settings(self._config(f"[Adapter]\nContactMode={alias}\n"))
            self.assertEqual(settings.contact_mode, "NC")
        for alias in ("no", "Normally_Open", "normally-open"):
            settings = load_cerbo_gx_relay_switch_settings(self._config(f"[Adapter]\nContactMode={alias}\n"))
            self.assertEqual(settings.contact_mode, "NO")

    def test_configured_settings_are_normalized(self) -> None:
        settings = load_cerbo_gx_relay_switch_settings(
            self._config(
                "[Adapter]\n"
                "RelayIndex=1\n"
                "ContactMode=NC\n"
                "EnsureManualFunction=0\n"
                "VerifySettleSeconds=1.25\n"
                "VerifyRetrySeconds=2.5\n"
                "[Capabilities]\n"
                "SupportedPhaseSelections=P1,P1_P2\n"
                "RequiresChargePauseForPhaseChange=1\n"
            )
        )
        self.assertEqual(settings.relay_index, 1)
        self.assertEqual(settings.contact_mode, "NC")
        self.assertFalse(settings.ensure_manual_function)
        self.assertEqual(settings.verify_settle_seconds, 1.25)
        self.assertEqual(settings.verify_retry_seconds, 2.5)
        self.assertEqual(settings.supported_phase_selections, ("P1", "P1_P2"))
        self.assertTrue(settings.requires_charge_pause_for_phase_change)

    def test_invalid_config_values_fail_or_use_timing_defaults(self) -> None:
        for value, message in (
            ("2", "supports RelayIndex 0 or 1"),
            ("x", "requires RelayIndex 0 or 1"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                load_cerbo_gx_relay_switch_settings(self._config(f"[Adapter]\nRelayIndex={value}\n"))
        with self.assertRaisesRegex(ValueError, "requires ContactMode NO or NC"):
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nContactMode=bad\n"))

        settings = load_cerbo_gx_relay_switch_settings(
            self._config("[Adapter]\nVerifySettleSeconds=-1\nVerifyRetrySeconds=bad\n")
        )
        self.assertEqual((settings.verify_settle_seconds, settings.verify_retry_seconds), (0.1, 0.2))
        blank = load_cerbo_gx_relay_switch_settings(
            self._config("[Adapter]\nRelayIndex=\nContactMode=\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n")
        )
        self.assertEqual((blank.relay_index, blank.contact_mode), (0, "NO"))
        self.assertEqual((blank.verify_settle_seconds, blank.verify_retry_seconds), (0.0, 0.0))

        missing = "/tmp/venus-evcharger-missing-cerbo-relay.ini"
        with self.assertRaises(FileNotFoundError):
            load_cerbo_gx_relay_switch_settings(missing)

    def test_backend_requires_composed_semantic_gateway_port(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Semantic gateway operations are not configured"):
            CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"))

    def test_no_and_nc_read_mapping_use_semantic_cache(self) -> None:
        no_gateway = _GatewayOperations(relay_state=1)
        no_backend = CerboGxRelaySwitchBackend(self._service(no_gateway))
        self.assertTrue(no_backend.read_switch_state().enabled)
        self.assertEqual(no_gateway.read_calls, [(0, 1.0)])
        no_gateway.relay_state = None
        self.assertFalse(no_backend.read_switch_state().enabled)

        nc_gateway = _GatewayOperations(relay_state=0)
        nc_backend = CerboGxRelaySwitchBackend(
            self._service(nc_gateway),
            self._config("[Adapter]\nRelayIndex=1\nContactMode=NC\nVerifySettleSeconds=2\nVerifyRetrySeconds=3\n"),
        )
        state = nc_backend.read_switch_state()
        self.assertTrue(state.enabled)
        self.assertEqual(state.phase_selection, "P1")
        self.assertEqual(nc_gateway.read_calls, [(1, 5.0)])

    def test_set_enabled_forwards_only_semantic_arguments(self) -> None:
        gateway = _GatewayOperations()
        backend = CerboGxRelaySwitchBackend(
            self._service(gateway),
            self._config(
                "[Adapter]\nRelayIndex=1\nContactMode=NC\nEnsureManualFunction=0\n"
                "VerifySettleSeconds=1.5\nVerifyRetrySeconds=2.5\n"
            ),
        )
        backend.set_enabled(True)
        self.assertEqual(
            gateway.set_calls,
            [
                GxRelaySetRequest(
                    relay_index=1,
                    contact_mode="NC",
                    enabled=True,
                    ensure_manual=False,
                    verify_settle_seconds=1.5,
                    verify_retry_seconds=2.5,
                )
            ],
        )
        gateway.accepted = False
        with self.assertRaisesRegex(RuntimeError, "operation was rejected"):
            backend.set_enabled(False)

    def test_capabilities_phase_selection_and_registry(self) -> None:
        gateway = _GatewayOperations()
        config_path = self._config(
            "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\nRequiresChargePauseForPhaseChange=1\n"
        )
        backend = create_switch_backend(
            "cerbo_gx_relay_switch", self._service(gateway, requested_phase_selection="P1_P2"), config_path
        )
        self.assertIsInstance(backend, CerboGxRelaySwitchBackend)
        assert isinstance(backend, CerboGxRelaySwitchBackend)
        capabilities = backend.capabilities()
        self.assertEqual(capabilities.switching_mode, "contactor")
        self.assertEqual(capabilities.supported_phase_selections, ("P1", "P1_P2"))
        self.assertTrue(capabilities.requires_charge_pause_for_phase_change)
        self.assertIsNone(capabilities.max_direct_switch_power_w)
        self.assertEqual(backend.read_switch_state().phase_selection, "P1_P2")
        backend.set_phase_selection("P1")
        self.assertEqual(backend.read_switch_state().phase_selection, "P1")
        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            backend.set_phase_selection("P1_P2_P3")


if __name__ == "__main__":
    unittest.main()
