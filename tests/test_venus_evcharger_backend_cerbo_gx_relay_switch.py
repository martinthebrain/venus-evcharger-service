# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import venus_evcharger.backend.cerbo_gx_relay_switch as cerbo_module
from venus_evcharger.backend.cerbo_gx_relay_switch import (
    CerboGxRelaySwitchBackend,
    load_cerbo_gx_relay_switch_settings,
)
from venus_evcharger.backend.registry import create_switch_backend
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox, dbus_path_key, gateway_paths


class TestCerboGxRelaySwitchBackend(unittest.TestCase):
    def _config(self, text: str) -> str:
        temp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        with temp:
            temp.write(text)
        return temp.name

    def _service(self, directory: str, *, run_dir: str | None = None) -> SimpleNamespace:
        paths = gateway_paths(run_dir or str(Path(directory) / "run"))
        return SimpleNamespace(
            dbus_gateway_run_dir=paths.run_dir,
            dbus_gateway_cache_path=paths.cache_path,
            requested_phase_selection="P1",
        )

    def _store(self, directory: str, *, run_dir: str | None = None) -> DbusCacheStore:
        return DbusCacheStore(gateway_paths(run_dir or str(Path(directory) / "run")))

    def _seed_cache(self, directory: str, values: dict[tuple[str, str], object], *, run_dir: str | None = None) -> None:
        store = self._store(directory, run_dir=run_dir)
        for (service, path), value in values.items():
            store.update_value(dbus_path_key(service, path), value, source=f"{service}{path}")
        store.write_snapshot_files()

    def _commands(self, directory: str, *, run_dir: str | None = None) -> list[dict[str, object]]:
        paths = gateway_paths(run_dir or str(Path(directory) / "run"))
        return [payload for _path, payload in DbusCommandInbox(paths.command_dir).load_pending()]

    def _has_command(self, commands: list[dict[str, object]], service: str, path: str, value: int) -> bool:
        return any(
            command.get("kind") == "set_value"
            and command.get("service") == service
            and command.get("path") == path
            and command.get("value") == value
            for command in commands
        )

    def test_no_contact_enqueues_manual_function_and_relay_on(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config(
                "[Adapter]\n"
                "Type=cerbo_gx_relay_switch\n"
                "RelayIndex=0\n"
                "ContactMode=NO\n"
                "VerifySettleSeconds=0\n"
                "VerifyRetrySeconds=0\n"
            )
            self._seed_cache(
                temp_dir,
                {("com.victronenergy.settings", "/Settings/Relay/0/Function"): 0},
            )
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)

            backend.set_enabled(True)
            self._seed_cache(temp_dir, {("com.victronenergy.system", "/Relay/0/State"): 1})
            state = backend.read_switch_state()

            commands = self._commands(temp_dir)
            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1")
            self.assertIsNone(state.feedback_closed)
            self.assertIsNone(state.interlock_ok)
            self.assertTrue(self._has_command(commands, "com.victronenergy.settings", "/Settings/Relay/0/Function", 2))
            self.assertTrue(self._has_command(commands, "com.victronenergy.system", "/Relay/0/State", 1))

    def test_nc_contact_inverts_enabled_mapping_and_supports_relay_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config(
                "[Adapter]\n"
                "Type=cerbo_gx_relay_switch\n"
                "RelayIndex=1\n"
                "ContactMode=NC\n"
                "VerifySettleSeconds=0\n"
                "VerifyRetrySeconds=0\n"
            )
            self._seed_cache(
                temp_dir,
                {
                    ("com.victronenergy.settings", "/Settings/Relay/1/Function"): 2,
                    ("com.victronenergy.system", "/Relay/1/State"): 0,
                },
            )
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)

            self.assertTrue(backend.read_switch_state().enabled)
            backend.set_enabled(False)

            commands = self._commands(temp_dir)
            self.assertTrue(self._has_command(commands, "com.victronenergy.system", "/Relay/1/State", 1))
            self.assertFalse(self._has_command(commands, "com.victronenergy.settings", "/Settings/Relay/Function", 2))

    def test_manual_function_cache_hit_avoids_settings_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config("[Adapter]\nRelayIndex=0\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n")
            self._seed_cache(temp_dir, {("com.victronenergy.settings", "/Settings/Relay/0/Function"): 2})
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)

            backend.set_enabled(False)

            commands = self._commands(temp_dir)
            self.assertFalse(self._has_command(commands, "com.victronenergy.settings", "/Settings/Relay/0/Function", 2))
            self.assertTrue(self._has_command(commands, "com.victronenergy.system", "/Relay/0/State", 0))

    def test_missing_manual_function_cache_enqueues_gateway_refresh_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config("[Adapter]\nRelayIndex=0\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n")
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)

            backend.set_enabled(False)

            commands = self._commands(temp_dir)
            self.assertTrue(
                any(
                    command.get("kind") == "refresh_value"
                    and command.get("service") == "com.victronenergy.settings"
                    and command.get("path") == "/Settings/Relay/0/Function"
                    for command in commands
                )
            )
            self.assertTrue(self._has_command(commands, "com.victronenergy.settings", "/Settings/Relay/0/Function", 2))

    def test_set_retries_and_raises_when_verify_readback_stays_wrong(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config(
                "[Adapter]\nEnsureManualFunction=0\nVerifySettleSeconds=0.01\nVerifyRetrySeconds=0.02\n"
            )
            self._seed_cache(temp_dir, {("com.victronenergy.system", "/Relay/0/State"): 0})
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)

            with patch.object(backend, "_cache_entry_is_stale_for_write", return_value=False), patch(
                "venus_evcharger.backend.cerbo_gx_relay_switch.time.sleep"
            ) as sleep_mock:
                with self.assertRaisesRegex(RuntimeError, "stayed at"):
                    backend.set_enabled(True)

            self.assertEqual([call.args[0] for call in sleep_mock.call_args_list], [0.01, 0.02, 0.01])

    def test_verify_skips_when_gateway_cache_has_no_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config("[Adapter]\nEnsureManualFunction=0\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n")
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)

            backend.set_enabled(True)

            self.assertTrue(self._has_command(self._commands(temp_dir), "com.victronenergy.system", "/Relay/0/State", 1))

    def test_config_validation_and_registry(self) -> None:
        with self.assertRaises(ValueError) as relay_range_error:
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nRelayIndex=2\n"))
        self.assertEqual(str(relay_range_error.exception), "Cerbo GX relay backend supports RelayIndex 0 or 1")

        with self.assertRaises(ValueError) as relay_parse_error:
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nRelayIndex=x\n"))
        self.assertEqual(str(relay_parse_error.exception), "Cerbo GX relay backend requires RelayIndex 0 or 1")

        with self.assertRaises(ValueError) as contact_mode_error:
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nContactMode=bad\n"))
        self.assertEqual(str(contact_mode_error.exception), "Cerbo GX relay backend requires ContactMode NO or NC")

        missing_path = "/tmp/venus-evcharger-missing-cerbo-relay.ini"
        with self.assertRaises(FileNotFoundError) as missing_config:
            load_cerbo_gx_relay_switch_settings(missing_path)
        self.assertEqual(missing_config.exception.args, (missing_path,))

        settings = load_cerbo_gx_relay_switch_settings(
            self._config("[Adapter]\nVerifySettleSeconds=-1\nVerifyRetrySeconds=bad\n")
        )
        self.assertEqual(settings.verify_settle_seconds, 0.1)
        self.assertEqual(settings.verify_retry_seconds, 0.2)
        zero_settings = load_cerbo_gx_relay_switch_settings(
            self._config("[Adapter]\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n")
        )
        self.assertEqual(zero_settings.verify_settle_seconds, 0.0)
        self.assertEqual(zero_settings.verify_retry_seconds, 0.0)

        backend = create_switch_backend("cerbo_gx_relay_switch", self._service(tempfile.gettempdir()), "")
        self.assertIsInstance(backend, CerboGxRelaySwitchBackend)
        capabilities = backend.capabilities()
        self.assertEqual(capabilities.switching_mode, "contactor")
        self.assertEqual(capabilities.supported_phase_selections, ("P1",))
        self.assertIs(capabilities.requires_charge_pause_for_phase_change, False)
        self.assertIsNone(capabilities.max_direct_switch_power_w)

    def test_settings_contract_preserves_all_configured_fields(self) -> None:
        config_path = self._config(
            "[Adapter]\n"
            "RelayIndex=1\n"
            "ContactMode=Normally_Closed\n"
            "EnsureManualFunction=0\n"
            "ManualFunctionValue=3\n"
            "VerifySettleSeconds=1.25\n"
            "VerifyRetrySeconds=2.5\n"
            "[Capabilities]\n"
            "SupportedPhaseSelections=P1,P1_P2\n"
            "RequiresChargePauseForPhaseChange=1\n"
        )

        settings = load_cerbo_gx_relay_switch_settings(config_path)

        self.assertEqual(settings.relay_index, 1)
        self.assertEqual(settings.contact_mode, "NC")
        self.assertIs(settings.ensure_manual_function, False)
        self.assertEqual(settings.manual_function_value, 3)
        self.assertEqual(settings.verify_settle_seconds, 1.25)
        self.assertEqual(settings.verify_retry_seconds, 2.5)
        self.assertEqual(settings.supported_phase_selections, ("P1", "P1_P2"))
        self.assertIs(settings.requires_charge_pause_for_phase_change, True)

        with tempfile.TemporaryDirectory() as temp_dir:
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)
            capabilities = backend.capabilities()
            self.assertEqual(capabilities.supported_phase_selections, ("P1", "P1_P2"))
            self.assertIs(capabilities.requires_charge_pause_for_phase_change, True)

    def test_supported_phase_selection_default_is_passed_explicitly(self) -> None:
        with patch.object(
            cerbo_module,
            "normalize_phase_selection_tuple",
            return_value=("P1",),
        ) as normalize:
            settings = load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nRelayIndex=0\n"))

        self.assertEqual(settings.supported_phase_selections, ("P1",))
        normalize.assert_called_once_with(None, ("P1",))

    def test_default_settings_contract_and_contact_mode_aliases(self) -> None:
        default_settings = load_cerbo_gx_relay_switch_settings("")

        self.assertEqual(default_settings.relay_index, 0)
        self.assertEqual(default_settings.contact_mode, "NO")
        self.assertIs(default_settings.ensure_manual_function, True)
        self.assertEqual(default_settings.manual_function_value, 2)
        self.assertEqual(default_settings.verify_settle_seconds, 0.1)
        self.assertEqual(default_settings.verify_retry_seconds, 0.2)
        self.assertEqual(default_settings.supported_phase_selections, ("P1",))
        self.assertIs(default_settings.requires_charge_pause_for_phase_change, False)

        for raw_mode in ("NO", "Normally_Open", "normally-open"):
            with self.subTest(raw_mode=raw_mode):
                settings = load_cerbo_gx_relay_switch_settings(self._config(f"[Adapter]\nContactMode={raw_mode}\n"))
                self.assertEqual(settings.contact_mode, "NO")
        for raw_mode in ("NC", "Normally_Closed", "normally-closed"):
            with self.subTest(raw_mode=raw_mode):
                settings = load_cerbo_gx_relay_switch_settings(self._config(f"[Adapter]\nContactMode={raw_mode}\n"))
                self.assertEqual(settings.contact_mode, "NC")

    def test_adapter_config_keys_are_case_sensitive(self) -> None:
        settings = load_cerbo_gx_relay_switch_settings(
            self._config(
                "[Adapter]\n"
                "relayindex=1\n"
                "contactmode=NC\n"
                "ensuremanualfunction=0\n"
                "manualfunctionvalue=3\n"
                "verifysettleseconds=1.25\n"
                "verifyretryseconds=2.5\n"
                "[Capabilities]\n"
                "supportedphaseselections=P1_P2\n"
                "requireschargepauseforphasechange=1\n"
            )
        )

        self.assertEqual(settings.relay_index, 0)
        self.assertEqual(settings.contact_mode, "NO")
        self.assertIs(settings.ensure_manual_function, True)
        self.assertEqual(settings.manual_function_value, 2)
        self.assertEqual(settings.verify_settle_seconds, 0.1)
        self.assertEqual(settings.verify_retry_seconds, 0.2)
        self.assertEqual(settings.supported_phase_selections, ("P1",))
        self.assertIs(settings.requires_charge_pause_for_phase_change, False)

    def test_blank_settings_values_fall_back_to_contract_defaults(self) -> None:
        settings = load_cerbo_gx_relay_switch_settings(
            self._config(
                "[Adapter]\n"
                "RelayIndex=  \n"
                "ContactMode=  \n"
                "ManualFunctionValue=  \n"
            )
        )

        self.assertEqual(settings.relay_index, 0)
        self.assertEqual(settings.contact_mode, "NO")
        self.assertEqual(settings.manual_function_value, 2)

    def test_default_config_and_phase_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir))
            self.assertEqual(backend.config_path, "")
            self.assertEqual(backend.settings.relay_index, 0)
            backend.set_phase_selection("P1")
            self.assertEqual(backend.read_switch_state().phase_selection, "P1")
            with self.assertRaisesRegex(ValueError, "Unsupported phase selection 'P1_P2' for Cerbo GX relay backend"):
                backend.set_phase_selection("P1_P2")

    def test_phase_selection_defaults_to_first_supported_value_and_accepts_configured_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config("[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n")
            service = self._service(temp_dir)
            service.requested_phase_selection = "unsupported"
            backend = CerboGxRelaySwitchBackend(service, config_path)

            self.assertEqual(backend.read_switch_state().phase_selection, "P1")
            backend.set_phase_selection("P1_P2")

            self.assertEqual(backend.read_switch_state().phase_selection, "P1_P2")

            service.requested_phase_selection = "P1_P2"
            backend = CerboGxRelaySwitchBackend(service, config_path)
            self.assertEqual(backend.read_switch_state().phase_selection, "P1_P2")

            service_without_request = SimpleNamespace(
                dbus_gateway_run_dir=service.dbus_gateway_run_dir,
                dbus_gateway_cache_path=service.dbus_gateway_cache_path,
            )
            backend = CerboGxRelaySwitchBackend(service_without_request, config_path)
            self.assertEqual(backend.read_switch_state().phase_selection, "P1")

    def test_set_enabled_raises_when_gateway_command_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir_file = str(Path(temp_dir) / "not-a-dir")
            Path(run_dir_file).write_text("x", encoding="utf-8")
            config_path = self._config("[Adapter]\nEnsureManualFunction=0\nVerifySettleSeconds=0\nVerifyRetrySeconds=0\n")
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir, run_dir=run_dir_file), config_path)

            with self.assertRaisesRegex(RuntimeError, "DBus SetValue failed"):
                backend.set_enabled(True)

    def test_gateway_helpers_and_value_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config("[Adapter]\nEnsureManualFunction=0\n")
            backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), config_path)
            with self.assertRaises(RuntimeError) as system_bus_error:
                backend._system_bus()
            self.assertEqual(str(system_bus_error.exception), "Direct DBus access is disabled; use the DBus gateway adapter")
            with self.assertRaises(RuntimeError) as busitem_error:
                backend._busitem("svc", "/path")
            self.assertEqual(str(busitem_error.exception), "Direct DBus access is disabled; use the DBus gateway adapter")

            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)
            self.assertIs(backend._with_dbus_retry(lambda: True), True)
            self.assertIsNone(backend._dbus_get_value("svc", "/path"))
            commands = self._commands(temp_dir)
            self.assertTrue(
                any(command.get("kind") == "refresh_value" and command.get("service") == "svc" and command.get("path") == "/path" for command in commands)
            )

            class _TextValue:
                def __str__(self) -> str:
                    return "text-value"

            class _IntValue:
                def __int__(self) -> int:
                    return 7

                def __float__(self) -> float:
                    return 8.5

            class _FloatValue:
                def __int__(self) -> int:
                    raise TypeError("not an int")

                def __float__(self) -> float:
                    return 9.25

            self.assertEqual(backend._normalized_dbus_value("x"), "x")
            self.assertEqual(backend._normalized_dbus_value(1), 1)
            self.assertIs(backend._normalized_dbus_value(True), True)
            self.assertEqual(backend._normalized_dbus_value(_IntValue()), 7)
            self.assertEqual(backend._normalized_dbus_value(_FloatValue()), 9.25)
            self.assertEqual(backend._normalized_dbus_value(_TextValue()), "text-value")

    def test_gateway_paths_and_command_payload_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config("[Adapter]\nEnsureManualFunction=0\n")
            explicit_cache = str(Path(temp_dir) / "explicit-cache.json")
            service = SimpleNamespace(dbus_gateway_cache_path=explicit_cache, dbus_gateway_run_dir="")
            backend = CerboGxRelaySwitchBackend(service, config_path)

            self.assertEqual(backend._gateway_cache_path(), explicit_cache)
            self.assertEqual(backend._gateway_run_dir(), "/tmp/venus-evcharger")

            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)
            with patch("venus_evcharger.backend.cerbo_gx_relay_switch.time.time", return_value=123.4):
                self.assertTrue(backend._dbus_set_value("svc", "/path", True))

            commands = self._commands(temp_dir)
            self.assertEqual(len(commands), 1)
            command = commands[0]
            self.assertEqual(command["kind"], "set_value")
            self.assertEqual(command["source"], "cerbo-gx-relay-switch")
            self.assertEqual(command["service"], "svc")
            self.assertEqual(command["path"], "/path")
            self.assertEqual(command["value"], 1)
            self.assertEqual(command["priority"], "user")
            self.assertEqual(command["coalesce_key"], "svc:/path")
            self.assertEqual(command["queue_class"], "remote-write")
            self.assertEqual(command["lifecycle_state"], "queued")
            self.assertEqual(command["created_at"], 123.4)
            self.assertEqual(backend._last_gateway_write_at[("svc", "/path")], 123.4)

    def test_gateway_paths_fall_back_when_service_has_no_gateway_attributes(self) -> None:
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), "")

        self.assertEqual(backend._gateway_run_dir(), "/tmp/venus-evcharger")
        self.assertEqual(backend._gateway_cache_path(), gateway_paths("/tmp/venus-evcharger").cache_path)

    def test_cache_entry_staleness_contract_for_gateway_writes(self) -> None:
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), "")

        self.assertFalse(backend._cache_entry_is_stale_for_write("svc", "/path"))
        backend._last_gateway_write_at[("svc", "/path")] = 0.5
        with patch.object(backend, "_dbus_value_entry", return_value=None):
            self.assertTrue(backend._cache_entry_is_stale_for_write("svc", "/path"))
        with patch.object(backend, "_dbus_value_entry", return_value={}):
            self.assertTrue(backend._cache_entry_is_stale_for_write("svc", "/path"))
        backend._last_gateway_write_at[("svc", "/path")] = 100.0
        with patch.object(backend, "_dbus_value_entry", return_value=None):
            self.assertTrue(backend._cache_entry_is_stale_for_write("svc", "/path"))
            backend._dbus_value_entry.assert_called_with("svc", "/path")
        with patch.object(backend, "_dbus_value_entry", return_value={"updated_at": 99.9}):
            self.assertTrue(backend._cache_entry_is_stale_for_write("svc", "/path"))
        with patch.object(backend, "_dbus_value_entry", return_value={"updated_at": 100.0}):
            self.assertTrue(backend._cache_entry_is_stale_for_write("svc", "/path"))
        with patch.object(backend, "_dbus_value_entry", return_value={"updated_at": 100.1}):
            self.assertFalse(backend._cache_entry_is_stale_for_write("svc", "/path"))
        with patch.object(backend, "_dbus_value_entry", return_value={"updated_at": "bad"}):
            self.assertTrue(backend._cache_entry_is_stale_for_write("svc", "/path"))

    def test_manual_function_error_preserves_optional_cause(self) -> None:
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), "")

        with self.assertRaisesRegex(RuntimeError, "Unable to set Cerbo GX relay 0 to manual function") as without_cause:
            backend._raise_manual_function_error(None)
        self.assertIsNone(without_cause.exception.__cause__)

        cause = OSError("gateway unavailable")
        with self.assertRaisesRegex(RuntimeError, "Unable to set Cerbo GX relay 0 to manual function") as with_cause:
            backend._raise_manual_function_error(cause)
        self.assertIs(with_cause.exception.__cause__, cause)

    def test_manual_function_failure_without_io_error_keeps_plain_runtime_error(self) -> None:
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), "")

        with patch.object(backend, "_manual_function_paths", return_value=("/path",)), patch.object(
            backend, "_manual_function_matches", return_value=False
        ), patch.object(backend, "_set_manual_function_path", return_value=False):
            with self.assertRaises(RuntimeError) as context:
                backend._ensure_manual_function()

        self.assertEqual(str(context.exception), "Unable to set Cerbo GX relay 0 to manual function")
        self.assertIsNone(context.exception.__cause__)

    def test_manual_function_continues_after_io_error_and_preserves_last_error(self) -> None:
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), "")
        first_error = OSError("first path unavailable")
        second_error = OSError("second path unavailable")

        with patch.object(backend, "_manual_function_paths", return_value=("/legacy", "/relay0")), patch.object(
            backend, "_manual_function_matches", side_effect=[first_error, False]
        ), patch.object(backend, "_set_manual_function_path", return_value=True) as set_path:
            backend._ensure_manual_function()
        set_path.assert_called_once_with("/relay0")

        with patch.object(backend, "_manual_function_paths", return_value=("/legacy", "/relay0")), patch.object(
            backend, "_manual_function_matches", side_effect=[first_error, second_error]
        ), patch.object(backend, "_set_manual_function_path") as set_path:
            with self.assertRaises(RuntimeError) as context:
                backend._ensure_manual_function()
        set_path.assert_not_called()
        self.assertIs(context.exception.__cause__, second_error)

    def test_manual_function_paths_contract_for_relay_zero_and_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            relay_zero = CerboGxRelaySwitchBackend(self._service(temp_dir), "")
            relay_one = CerboGxRelaySwitchBackend(
                self._service(temp_dir),
                self._config("[Adapter]\nRelayIndex=1\n"),
            )

            self.assertEqual(relay_zero._manual_function_paths(), ("/Settings/Relay/0/Function", "/Settings/Relay/Function"))
            self.assertEqual(relay_one._manual_function_paths(), ("/Settings/Relay/1/Function",))

    def test_gateway_refresh_request_payload_contract_on_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._config("[Adapter]\nEnsureManualFunction=0\n")
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), config_path)

            self.assertIsNone(backend._dbus_get_value("svc", "/path"))

            commands = self._commands(temp_dir)
            self.assertEqual(len(commands), 1)
            command = commands[0]
            self.assertEqual(command["kind"], "refresh_value")
            self.assertEqual(command["source"], "cerbo-gx-relay-switch")
            self.assertEqual(command["service"], "svc")
            self.assertEqual(command["path"], "/path")
            self.assertEqual(command["priority"], "read")
            self.assertEqual(command["reason"], "cerbo gx relay cache miss")

    def test_gateway_refresh_request_calls_client_with_explicit_priority(self) -> None:
        class _GatewayClient:
            def __init__(self) -> None:
                self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

            def request_raw_value(self, *args: object, **kwargs: object) -> None:
                self.calls.append((args, kwargs))

        gateway_client = _GatewayClient()
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), "")

        with patch.object(backend, "_gateway_client", return_value=gateway_client):
            self.assertIsNone(backend._dbus_get_value("svc", "/path"))

        self.assertEqual(gateway_client.calls, [(("svc", "/path"), {
            "priority": "read",
            "reason": "cerbo gx relay cache miss",
            "source": "cerbo-gx-relay-switch",
        })])

    def test_verify_relay_state_skips_retry_on_matching_or_unknown_readback(self) -> None:
        backend = CerboGxRelaySwitchBackend(SimpleNamespace(requested_phase_selection="P1"), "")

        with patch.object(backend, "_verified_relay_readback_or_none", return_value=1), patch.object(
            backend, "_retry_relay_state"
        ) as retry:
            backend._verify_relay_state(1)
        retry.assert_not_called()

        with patch.object(backend, "_verified_relay_readback_or_none", return_value=None), patch.object(
            backend, "_retry_relay_state"
        ) as retry:
            backend._verify_relay_state(1)
        retry.assert_not_called()

        with patch.object(backend, "_verified_relay_readback_or_none", side_effect=[0, None]), patch.object(
            backend, "_retry_relay_state"
        ) as retry:
            backend._verify_relay_state(1)
        retry.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
