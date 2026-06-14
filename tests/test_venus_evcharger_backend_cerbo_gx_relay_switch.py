# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
        with self.assertRaises(ValueError):
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nRelayIndex=2\n"))
        with self.assertRaises(ValueError):
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nRelayIndex=x\n"))
        with self.assertRaises(ValueError):
            load_cerbo_gx_relay_switch_settings(self._config("[Adapter]\nContactMode=bad\n"))
        with self.assertRaises(FileNotFoundError):
            load_cerbo_gx_relay_switch_settings("/tmp/venus-evcharger-missing-cerbo-relay.ini")

        settings = load_cerbo_gx_relay_switch_settings(
            self._config("[Adapter]\nVerifySettleSeconds=-1\nVerifyRetrySeconds=bad\n")
        )
        self.assertEqual(settings.verify_settle_seconds, 0.1)
        self.assertEqual(settings.verify_retry_seconds, 0.2)

        backend = create_switch_backend("cerbo_gx_relay_switch", self._service(tempfile.gettempdir()), "")
        self.assertIsInstance(backend, CerboGxRelaySwitchBackend)
        self.assertEqual(backend.capabilities().switching_mode, "contactor")

    def test_default_config_and_phase_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend = CerboGxRelaySwitchBackend(self._service(temp_dir), "")
            self.assertEqual(backend.settings.relay_index, 0)
            backend.set_phase_selection("P1")
            with self.assertRaises(ValueError):
                backend.set_phase_selection("P1_P2")

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
            with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
                backend._system_bus()
            with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
                backend._busitem("svc", "/path")

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

            self.assertEqual(backend._normalized_dbus_value("x"), "x")
            self.assertEqual(backend._normalized_dbus_value(1), 1)
            self.assertEqual(backend._normalized_dbus_value(_TextValue()), "text-value")


if __name__ == "__main__":
    unittest.main()
