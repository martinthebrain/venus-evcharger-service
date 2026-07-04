# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_sources_cases_common import *
import json
import os
import tempfile
from venus_evcharger.core.shared import compact_json
from venus_evcharger.dbus_gateway import (
    BATTERY_SOC_READ_KEY,
    GRID_POWER_READ_KEY,
    PV_POWER_READ_KEY,
    DbusCacheStore,
    DbusCommandInbox,
    GatewayClient,
    dbus_path_key,
    gateway_paths,
)
from venus_evcharger.inputs.helper import sources_dbus_gateway as sources_gateway_module


class _AutoInputHelperSourcesDbusCases:
    def _prepare_gateway(self, helper):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        paths = gateway_paths(os.path.join(tempdir.name, "run"))
        helper.dbus_gateway_run_dir = paths.run_dir
        helper.dbus_gateway_cache_path = paths.cache_path
        helper.dbus_gateway_max_age_seconds = 30.0
        helper._gateway_client_instance = None
        return paths

    def _write_gateway_cache(self, helper, *, values=None, key_values=None, services=None):
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths)
        for (service_name, path), value in (values or {}).items():
            store.update_value(dbus_path_key(service_name, path), value, source=f"{service_name}{path}")
        for key, value in (key_values or {}).items():
            store.update_value(str(key), value, source=f"read-key:{key}")
        if services is not None:
            store.update_services(list(services))
        store.write_snapshot_files()
        return paths

    def _gateway_commands(self, paths):
        return [command for _, command in DbusCommandInbox(paths.command_dir).load_pending()]

    def test_parent_watchdog_quits_mainloop_when_parent_disappears(self):
        helper = self._make_helper()
        helper._stop_requested = False
        helper._parent_alive = MagicMock(return_value=False)
        helper._main_loop = MagicMock()

        self.assertFalse(helper._parent_watchdog())
        helper._main_loop.quit.assert_called_once_with()

        helper = self._make_helper()
        helper._stop_requested = True
        helper._parent_alive = MagicMock(return_value=False)
        self.assertFalse(helper._parent_watchdog())

    def test_list_dbus_services_returns_empty_and_sets_backoff_on_failure(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._list_dbus_services(), [])

        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["kind"], "refresh_services")
        self.assertEqual(helper._dbus_list_failures, 1)
        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

    def test_list_dbus_services_skips_backoff_cap_when_maximum_is_disabled(self):
        helper = self._make_helper()
        helper.auto_dbus_backoff_base_seconds = 5.0
        helper.auto_dbus_backoff_max_seconds = 0.0
        self._prepare_gateway(helper)
        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._list_dbus_services(), [])

        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

    def test_get_system_bus_is_disabled_for_helper_process(self):
        helper = self._make_helper()
        with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
            helper._get_system_bus()
        with self.assertRaises(RuntimeError) as raised:
            helper._dbus_module()
        self.assertEqual(str(raised.exception), "Direct DBus access is disabled; use the DBus gateway adapter")
        with self.assertRaises(RuntimeError) as raised:
            sources_gateway_module._AutoInputHelperSourceDbusGateway._dbus_module()
        self.assertEqual(str(raised.exception), "Direct DBus access is disabled; use the DBus gateway adapter")

    def test_get_dbus_value_reads_gateway_cache_and_child_nodes_use_introspection_snapshot(self):
        helper = self._make_helper()
        self._write_gateway_cache(helper, values={("svc", "/Path"): 42.0})
        helper._gateway_client_instance = object()
        self.assertIsInstance(helper._gateway_client(), GatewayClient)
        self.assertIs(helper._gateway_client(), helper._gateway_client_instance)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write(
                compact_json(
                    {
                        "schema_version": 1,
                        "captured_at": 100.0,
                        "heartbeat_at": 100.0,
                        "services": {
                            "svc": {
                                "paths": {
                                    "/Ac/Grid": {
                                        "status": "fresh",
                                        "children": ["L1", "L2"],
                                    }
                                }
                            }
                        },
                    }
                )
            )
        try:
            self.assertEqual(helper._get_dbus_value("svc", "/Path"), 42.0)
            helper.dbus_introspection_snapshot_path = handle.name
            helper._dbus_introspection_snapshot_loaded_at = 0.0
            with patch("venus_evcharger.dbus_introspection.time.time", return_value=120.0):
                self.assertEqual(helper._get_dbus_child_nodes("svc", "/Ac/Grid"), ["L1", "L2"])
        finally:
            os.unlink(helper.dbus_introspection_snapshot_path)

    def test_gateway_client_and_cache_snapshot_contracts_are_exact(self):
        helper = self._make_helper()
        self.assertEqual(helper._gateway_client().paths.run_dir, gateway_paths("").run_dir)

        helper = self._make_helper()
        helper.dbus_gateway_run_dir = ""
        self.assertEqual(helper._gateway_client().paths.run_dir, gateway_paths("").run_dir)

        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        first_client = helper._gateway_client()
        helper.dbus_gateway_run_dir = "/different/run-dir"
        self.assertIs(helper._gateway_client(), first_client)
        self.assertEqual(first_client.paths.run_dir, paths.run_dir)

        helper._gateway_client_instance = object()
        replacement_client = helper._gateway_client()
        self.assertIsInstance(replacement_client, GatewayClient)
        self.assertIs(helper._gateway_client_instance, replacement_client)

        helper = self._make_helper()
        helper.dbus_gateway_cache_path = "/tmp/explicit-cache.json"
        helper.dbus_gateway_max_age_seconds = 17.0
        helper._gateway_client = MagicMock(side_effect=AssertionError("explicit cache path should not need client fallback"))
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.DbusCacheStore.load_snapshot",
            return_value={"values": {}},
        ) as load_snapshot:
            self.assertEqual(helper._gateway_cache_snapshot(), {"values": {}})
        load_snapshot.assert_called_once_with("/tmp/explicit-cache.json", max_age_seconds=17.0)

        helper = self._make_helper()
        helper.dbus_gateway_cache_path = ""
        helper.dbus_gateway_max_age_seconds = 11.0
        fallback_client = MagicMock()
        fallback_client.paths.cache_path = "/tmp/fallback-cache.json"
        helper._gateway_client = MagicMock(return_value=fallback_client)
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.DbusCacheStore.load_snapshot",
            return_value={"services": {}},
        ) as load_snapshot:
            self.assertEqual(helper._gateway_cache_snapshot(), {"services": {}})
        load_snapshot.assert_called_once_with("/tmp/fallback-cache.json", max_age_seconds=11.0)

    def test_get_dbus_value_ignores_fresh_non_numeric_gateway_values(self):
        helper = self._make_helper()
        paths = self._write_gateway_cache(helper, values={("svc", "/Path"): "not-numeric"})

        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        self.assertEqual(self._gateway_commands(paths), [])

    def test_get_dbus_value_rejects_stale_gateway_cache_and_requests_refresh(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths, stale_after_seconds=1.0)
        store.update_value(dbus_path_key("svc", "/Path"), 42.0, source="svc/Path", now=100.0)
        with patch("venus_evcharger.dbus_gateway_cache._now", return_value=200.0):
            store.write_snapshot_files()

        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["kind"], "refresh_value")
        self.assertEqual(commands[0]["service"], "svc")
        self.assertEqual(commands[0]["path"], "/Path")

    def test_semantic_pv_read_uses_gateway_key_not_raw_dbus_paths(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(return_value=123.0)
        self._write_gateway_cache(helper, key_values={PV_POWER_READ_KEY: 123.0})

        self.assertEqual(helper._get_pv_power(), 123.0)
        helper._get_dbus_value.assert_not_called()

    def test_semantic_pv_and_grid_reads_use_exact_gateway_contracts(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=123)
        helper._delay_source_retry = MagicMock()

        self.assertEqual(helper._get_pv_power(), 123.0)
        helper._get_gateway_read_value.assert_called_once_with(
            PV_POWER_READ_KEY,
            reason="helper semantic PV power read",
        )
        helper._delay_source_retry.assert_not_called()

        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=-45)
        helper._delay_source_retry = MagicMock()

        self.assertEqual(helper._get_grid_power(), -45.0)
        helper._get_gateway_read_value.assert_called_once_with(
            GRID_POWER_READ_KEY,
            reason="helper semantic grid power read",
        )
        helper._delay_source_retry.assert_not_called()

    def test_semantic_pv_and_grid_reads_delay_only_the_missing_source(self):
        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()

        self.assertIsNone(helper._get_pv_power())
        helper._get_gateway_read_value.assert_called_once_with(
            PV_POWER_READ_KEY,
            reason="helper semantic PV power read",
        )
        helper._delay_source_retry.assert_called_once_with("pv")

        helper = self._make_helper()
        helper._get_gateway_read_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()

        self.assertIsNone(helper._get_grid_power())
        helper._get_gateway_read_value.assert_called_once_with(
            GRID_POWER_READ_KEY,
            reason="helper semantic grid power read",
        )
        helper._delay_source_retry.assert_called_once_with("grid")

    def test_semantic_gateway_read_suppresses_fresh_cached_errors(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths)
        store.mark_error(PV_POWER_READ_KEY, source="read-key:pv", error="sleeping", now=100.0)
        store.write_snapshot_files()

        with (
            patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=101.0),
            patch("venus_evcharger.inputs.helper.sources_dbus_gateway.logging.debug") as debug,
        ):
            self.assertIsNone(helper._get_gateway_read_value(PV_POWER_READ_KEY, reason="pv retry"))

        self.assertEqual(self._gateway_commands(paths), [])
        debug.assert_called_once_with(
            "Auto helper suppressing fresh DBus cache error for read key %s",
            PV_POWER_READ_KEY,
        )

    def test_semantic_pv_and_grid_reads_do_not_query_gateway_during_retry_cooldown(self):
        helper = self._make_helper()
        helper._source_retry_after["pv"] = 200.0
        helper._get_gateway_read_value = MagicMock(side_effect=AssertionError("PV read should be skipped"))
        helper._delay_source_retry = MagicMock()
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_pv_power())
        helper._get_gateway_read_value.assert_not_called()
        helper._delay_source_retry.assert_not_called()

        helper = self._make_helper()
        helper._source_retry_after["grid"] = 200.0
        helper._get_gateway_read_value = MagicMock(side_effect=AssertionError("Grid read should be skipped"))
        helper._delay_source_retry = MagicMock()
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_grid_power())
        helper._get_gateway_read_value.assert_not_called()
        helper._delay_source_retry.assert_not_called()

    def test_get_dbus_value_skips_known_unusable_introspection_finding(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as snapshot:
            snapshot.write(
                compact_json(
                    {
                        "schema_version": 1,
                        "captured_at": 100.0,
                        "heartbeat_at": 100.0,
                        "services": {
                            "svc.dead": {
                                "paths": {
                                    "/Soc": {
                                        "status": "known-missing",
                                        "retry_after": 0.0,
                                    }
                                }
                            }
                        },
                    }
                )
            )
            helper.dbus_introspection_snapshot_path = snapshot.name
        try:
            with patch("venus_evcharger.dbus_introspection.time.time", return_value=120.0):
                self.assertIsNone(helper._get_dbus_value("svc.dead", "/Soc"))
        finally:
            os.unlink(snapshot.name)
        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["source"], "auto-input-helper")
        self.assertEqual(commands[0]["kind"], "introspect")

    def test_get_dbus_value_and_child_nodes_do_not_reset_on_missing_services(self):
        helper = self._make_helper()
        helper._reset_system_bus = MagicMock()
        paths = self._prepare_gateway(helper)
        self.assertIsNone(helper._get_dbus_value("com.example.missing", "/Soc"))
        self.assertEqual(helper._get_dbus_child_nodes("com.example.missing", "/"), [])

        helper._reset_system_bus.assert_not_called()
        kinds = [command["kind"] for command in self._gateway_commands(paths)]
        self.assertCountEqual(kinds, ["refresh_value", "introspect"])

    def test_get_dbus_value_and_child_nodes_request_gateway_on_cache_miss(self):
        helper = self._make_helper()
        helper._reset_system_bus = MagicMock()
        paths = self._prepare_gateway(helper)
        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        self.assertEqual(helper._get_dbus_child_nodes("svc", "/Path"), [])

        helper._reset_system_bus.assert_not_called()
        kinds = [command["kind"] for command in self._gateway_commands(paths)]
        self.assertCountEqual(kinds, ["refresh_value", "introspect"])

    def test_get_dbus_value_read_contracts_are_exact(self):
        helper = self._make_helper()
        helper._dbus_introspection_says_skip = MagicMock(return_value=True)
        helper._request_dbus_introspection = MagicMock()
        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        helper._dbus_introspection_says_skip.assert_called_once_with("svc", "/Path")
        helper._request_dbus_introspection.assert_called_once_with(
            "svc",
            "/Path",
            priority=80,
            reason="helper skipped known-unusable path",
        )

        helper = self._make_helper()
        helper._dbus_introspection_says_skip = MagicMock(return_value=False)
        helper._gateway_cache_snapshot = MagicMock(
            return_value={
                "values": {
                    dbus_path_key("svc", "/Path"): {"status": "fresh", "value": 7},
                    dbus_path_key("other", "/Path"): {"status": "fresh", "value": 99},
                }
            }
        )
        helper._request_gateway_value = MagicMock()
        self.assertEqual(helper._get_dbus_value("svc", "/Path"), 7)
        helper._request_gateway_value.assert_not_called()

        helper = self._make_helper()
        helper._dbus_introspection_says_skip = MagicMock(return_value=False)
        helper._gateway_cache_snapshot = MagicMock(return_value={"values": {}})
        helper._request_gateway_value = MagicMock()
        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        helper._request_gateway_value.assert_called_once_with(
            "svc",
            "/Path",
            priority=90,
            reason="helper DBus cache miss",
        )

        helper = self._make_helper()
        helper._dbus_introspection_says_skip = MagicMock(return_value=False)
        helper._cached_gateway_error_recent = MagicMock(return_value=True)
        helper._gateway_cache_snapshot = MagicMock(
            return_value={"values": {dbus_path_key("svc", "/Path"): {"status": "error", "error_at": 100.0}}}
        )
        helper._request_gateway_value = MagicMock()
        self.assertIsNone(helper._get_dbus_value("svc", "/Path"))
        helper._request_gateway_value.assert_not_called()

    def test_gateway_read_value_requests_missing_semantic_key_with_exact_reason(self):
        helper = self._make_helper()
        helper._gateway_cache_snapshot = MagicMock(return_value={"values": {}})
        helper._cached_gateway_error_recent = MagicMock(return_value=False)
        helper._request_gateway_read_key = MagicMock()

        self.assertIsNone(helper._get_gateway_read_value(PV_POWER_READ_KEY, reason="exact semantic reason"))
        helper._request_gateway_read_key.assert_called_once_with(
            PV_POWER_READ_KEY,
            reason="exact semantic reason",
        )

    def test_child_node_and_introspection_skip_contracts_are_exact(self):
        helper = self._make_helper()
        helper._request_dbus_introspection = MagicMock()
        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.owner_path_children",
            return_value=["L1", "L2"],
        ) as children:
            self.assertEqual(helper._get_dbus_child_nodes("svc", "/Ac/Grid"), ["L1", "L2"])
        children.assert_called_once_with(helper, "svc", "/Ac/Grid")
        helper._request_dbus_introspection.assert_not_called()

        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.owner_path_children",
            return_value=[],
        ) as children:
            self.assertEqual(helper._get_dbus_child_nodes("svc", "/Ac/Grid"), [])
        children.assert_called_once_with(helper, "svc", "/Ac/Grid")
        helper._request_dbus_introspection.assert_called_once_with(
            "svc",
            "/Ac/Grid",
            priority=60,
            reason="helper child-node discovery requested",
        )

        with (
            patch(
                "venus_evcharger.inputs.helper.sources_dbus_gateway.owner_path_unusable",
                return_value=(True, "known missing"),
            ) as unusable,
            patch("venus_evcharger.inputs.helper.sources_dbus_gateway.logging.debug") as debug,
        ):
            self.assertTrue(helper._dbus_introspection_says_skip("svc", "/Missing"))
        unusable.assert_called_once_with(helper, "svc", "/Missing")
        debug.assert_called_once_with(
            "Auto helper skipping %s %s from DBus introspection cache: %s",
            "svc",
            "/Missing",
            "known missing",
        )

        with patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.owner_path_unusable",
            return_value=(False, "usable"),
        ):
            self.assertFalse(helper._dbus_introspection_says_skip("svc", "/Present"))

    def test_gateway_request_methods_emit_exact_command_contracts(self):
        helper = self._make_helper()
        client = MagicMock()
        helper._gateway_client = MagicMock(return_value=client)

        helper._request_gateway_value("svc", "/Path", priority=90, reason="urgent read")
        client.enqueue_command.assert_called_with(
            {
                "kind": "refresh_value",
                "source": "auto-input-helper",
                "service": "svc",
                "path": "/Path",
                "priority": "read",
                "reason": "urgent read",
                "coalesce_key": "refresh:svc:/Path",
            }
        )

        helper._request_gateway_value("svc", "/Boundary", priority=80, reason="boundary read")
        client.enqueue_command.assert_called_with(
            {
                "kind": "refresh_value",
                "source": "auto-input-helper",
                "service": "svc",
                "path": "/Boundary",
                "priority": "read",
                "reason": "boundary read",
                "coalesce_key": "refresh:svc:/Boundary",
            }
        )

        helper._request_gateway_value("svc", "/Optional", priority=79, reason="optional read")
        client.enqueue_command.assert_called_with(
            {
                "kind": "refresh_value",
                "source": "auto-input-helper",
                "service": "svc",
                "path": "/Optional",
                "priority": "optional",
                "reason": "optional read",
                "coalesce_key": "refresh:svc:/Optional",
            }
        )

        helper._request_dbus_introspection("svc", "/Tree", priority=60, reason="tree")
        client.enqueue_command.assert_called_with(
            {
                "kind": "introspect",
                "source": "auto-input-helper",
                "service": "svc",
                "path": "/Tree",
                "priority": "discovery",
                "reason": "tree",
                "coalesce_key": "introspect:svc:/Tree",
            }
        )

        helper._request_dbus_introspection("svc", "/FastTree", priority=90, reason="fast tree")
        client.enqueue_command.assert_called_with(
            {
                "kind": "introspect",
                "source": "auto-input-helper",
                "service": "svc",
                "path": "/FastTree",
                "priority": "read",
                "reason": "fast tree",
                "coalesce_key": "introspect:svc:/FastTree",
            }
        )

        helper._request_gateway_read_key(PV_POWER_READ_KEY, reason="semantic pv")
        client.request_read_key.assert_called_once_with(
            PV_POWER_READ_KEY,
            priority="read",
            source="auto-input-helper",
            reason="semantic pv",
        )

    def test_gateway_request_methods_treat_unavailable_inbox_as_best_effort(self):
        helper = self._make_helper()
        client = MagicMock()
        helper._gateway_client = MagicMock(return_value=client)
        client.enqueue_command.side_effect = OSError("no socket")
        client.request_read_key.side_effect = OSError("no socket")

        helper._request_gateway_value("svc", "/Path", priority=90, reason="miss")
        helper._request_dbus_introspection("svc", "/Path", priority=60, reason="tree")
        helper._request_gateway_read_key(PV_POWER_READ_KEY, reason="semantic")

        self.assertEqual(client.enqueue_command.call_count, 2)
        client.request_read_key.assert_called_once_with(
            PV_POWER_READ_KEY,
            priority="read",
            source="auto-input-helper",
            reason="semantic",
        )

    def test_get_dbus_value_suppresses_recent_gateway_error_until_retry_window(self):
        helper = self._make_helper()
        paths = self._prepare_gateway(helper)
        store = DbusCacheStore(paths)
        cache_key = dbus_path_key("svc", "/Missing")
        store.mark_error(cache_key, source="svc/Missing", error="missing", now=100.0)
        store.write_snapshot_files()
        helper.dbus_gateway_error_retry_seconds = 30.0

        with (
            unittest.mock.patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=120.0),
            patch("venus_evcharger.inputs.helper.sources_dbus_gateway.logging.debug") as debug,
        ):
            self.assertIsNone(helper._get_dbus_value("svc", "/Missing"))
        self.assertEqual(self._gateway_commands(paths), [])
        debug.assert_called_once_with(
            "Auto helper suppressing fresh DBus cache error for %s %s",
            "svc",
            "/Missing",
        )
        self.assertFalse(helper._gateway_error_recent({"status": "fresh", "error_at": 100.0}))
        self.assertFalse(helper._gateway_error_recent({"status": "error", "error_at": 0.0}))

        with unittest.mock.patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=131.0):
            self.assertIsNone(helper._get_dbus_value("svc", "/Missing"))
        commands = self._gateway_commands(paths)
        self.assertEqual(commands[0]["kind"], "refresh_value")

    def test_gateway_cache_and_retry_boundaries_are_explicit(self):
        helper = self._make_helper()

        self.assertEqual(helper._gateway_cache_max_age_seconds(), 10.0)
        helper.dbus_gateway_max_age_seconds = 2.5
        self.assertEqual(helper._gateway_cache_max_age_seconds(), 2.5)
        helper.dbus_gateway_max_age_seconds = -5.0
        self.assertEqual(helper._gateway_cache_max_age_seconds(), 0.0)
        helper.dbus_gateway_max_age_seconds = 0.0
        self.assertEqual(helper._gateway_cache_max_age_seconds(), 10.0)

        self.assertEqual(helper._gateway_error_retry_seconds(), 30.0)
        helper.dbus_gateway_error_retry_seconds = 0.5
        self.assertEqual(helper._gateway_error_retry_seconds(), 1.0)
        helper.dbus_gateway_error_retry_seconds = 2.5
        self.assertEqual(helper._gateway_error_retry_seconds(), 2.5)
        helper.dbus_gateway_error_retry_seconds = 600.0
        self.assertEqual(helper._gateway_error_retry_seconds(), 300.0)
        helper.dbus_gateway_error_retry_seconds = 0.0
        self.assertEqual(helper._gateway_error_retry_seconds(), 30.0)

        helper.dbus_gateway_error_retry_seconds = 30.0
        with unittest.mock.patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=130.0):
            self.assertFalse(helper._gateway_error_recent({"status": "error", "error_at": 100.0}))
        with unittest.mock.patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=129.999):
            self.assertTrue(helper._gateway_error_recent({"status": "error", "error_at": "100.0"}))
        with unittest.mock.patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=2.0):
            self.assertTrue(helper._gateway_error_recent({"status": "error", "error_at": 1.0}))
            self.assertFalse(helper._gateway_error_recent({"status": "error"}))
            self.assertFalse(helper._gateway_error_recent({"status": "error", "error_at": 0.0}))
        self.assertFalse(helper._gateway_error_recent({"status": "fresh", "error_at": 100.0}))
        self.assertFalse(helper._gateway_error_recent({"status": "error"}))
        self.assertFalse(helper._gateway_error_recent({"status": "error", "error_at": 0.0}))

    def test_cached_gateway_value_contracts_are_exact(self):
        missing = sources_gateway_module._CACHE_VALUE_MISSING

        self.assertIs(helper_value := self._make_helper()._cached_gateway_value(None), missing)
        self.assertIs(helper_value, missing)
        self.assertIs(self._make_helper()._cached_gateway_value({}), missing)
        self.assertIs(self._make_helper()._cached_gateway_value({"status": "error", "value": 12.0}), missing)
        self.assertEqual(self._make_helper()._cached_gateway_value({"status": "fresh", "value": "12.5"}), 12.5)

        helper = self._make_helper()
        self.assertEqual(helper._cached_gateway_numeric_value({"status": "fresh", "value": 12}), (True, 12))
        self.assertEqual(helper._cached_gateway_numeric_value({"status": "fresh", "value": "bad"}), (True, None))
        self.assertEqual(helper._cached_gateway_numeric_value({"status": "fresh", "value": True}), (True, None))
        self.assertEqual(helper._cached_gateway_numeric_value({"status": "stale", "value": 12}), (False, None))

    def test_list_dbus_services_short_circuits_during_backoff_and_resets_on_success(self):
        helper = self._make_helper()
        helper._gateway_cache_snapshot = MagicMock(side_effect=AssertionError("backoff must not inspect cache"))
        helper._gateway_client = MagicMock(side_effect=AssertionError("backoff must not enqueue"))
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=50.0):
            helper._dbus_list_backoff_until = 60.0
            self.assertEqual(helper._list_dbus_services(), [])

        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 2
        helper._gateway_cache_snapshot = MagicMock(return_value={"services": {1: {}, "com.victronenergy.system": {}}})
        helper._gateway_client = MagicMock(side_effect=AssertionError("fresh services cache must not enqueue"))
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._list_dbus_services(), ["1", "com.victronenergy.system"])

        self.assertEqual(helper._dbus_list_failures, 0)
        self.assertEqual(helper._dbus_list_backoff_until, 0.0)

    def test_list_dbus_services_request_contract_and_backoff_edges_are_exact(self):
        helper = self._make_helper()
        helper._gateway_cache_snapshot = MagicMock(return_value={})
        client = MagicMock()
        helper._gateway_client = MagicMock(return_value=client)

        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._list_dbus_services(), [])
        client.enqueue_command.assert_called_once_with(
            {
                "kind": "refresh_services",
                "source": "auto-input-helper",
                "priority": "discovery",
                "coalesce_key": "refresh-services",
            }
        )
        self.assertEqual(helper._dbus_list_failures, 1)
        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

        with patch("venus_evcharger_auto_input_helper.time.time", return_value=104.999):
            self.assertEqual(helper._list_dbus_services(), [])
        client.enqueue_command.assert_called_once()

        with patch("venus_evcharger_auto_input_helper.time.time", return_value=105.0):
            self.assertEqual(helper._list_dbus_services(), [])
        self.assertEqual(client.enqueue_command.call_count, 2)
        self.assertEqual(helper._dbus_list_failures, 2)
        self.assertEqual(helper._dbus_list_backoff_until, 115.0)

        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 4
        helper.auto_dbus_backoff_base_seconds = -5.0
        helper.auto_dbus_backoff_max_seconds = 0.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=200.0):
            self.assertEqual(helper._list_dbus_services(), [])
        self.assertEqual(helper._dbus_list_backoff_until, 200.0)

        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 0
        helper.auto_dbus_backoff_base_seconds = 5.0
        helper.auto_dbus_backoff_max_seconds = 0.5
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=300.0):
            self.assertEqual(helper._list_dbus_services(), [])
        self.assertEqual(helper._dbus_list_backoff_until, 300.5)

    def test_gateway_retry_read_and_reset_contracts_are_exact(self):
        helper = self._make_helper()
        helper._reset_system_bus_after_retryable_error = MagicMock()
        first_error = RuntimeError("temporary one")
        second_error = RuntimeError("temporary two")
        calls = []

        def fails_twice_then_would_succeed():
            calls.append("read")
            if len(calls) == 1:
                raise first_error
            if len(calls) == 2:
                raise second_error
            return 99

        with self.assertRaisesRegex(RuntimeError, "temporary two"):
            helper._dbus_retry_read("svc", "/Value", "label", fails_twice_then_would_succeed)
        self.assertEqual(calls, ["read", "read"])
        helper._reset_system_bus_after_retryable_error.assert_has_calls(
            [
                unittest.mock.call(0, "label", "svc", "/Value", first_error),
                unittest.mock.call(1, "label", "svc", "/Value", second_error),
            ]
        )

        helper = self._make_helper()
        helper._reset_system_bus_after_retryable_error = MagicMock()
        missing_error = RuntimeError("NameHasNoOwner")
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.logging.debug") as debug:
            with self.assertRaises(RuntimeError) as raised:
                helper._dbus_retry_read("missing-svc", "/Soc", "read", lambda: (_ for _ in ()).throw(missing_error))
        self.assertIs(raised.exception, missing_error)
        helper._reset_system_bus_after_retryable_error.assert_not_called()
        debug.assert_called_once_with(
            "DBus value missing for %s %s: %s",
            "missing-svc",
            "/Soc",
            missing_error,
        )

        helper = self._make_helper()
        helper._reset_system_bus = MagicMock()
        retry_error = RuntimeError("retryable")
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.logging.debug") as debug:
            helper._reset_system_bus_after_retryable_error(0, "grid", "svc", "/Path", retry_error)
            helper._reset_system_bus_after_retryable_error(1, "pv", "svc2", "/Other", RuntimeError("later"))
        self.assertEqual(helper._reset_system_bus.call_count, 2)
        debug.assert_called_once_with(
            "%s retry for %s %s after error: %s",
            "grid",
            "svc",
            "/Path",
            retry_error,
        )

    def test_source_retry_helpers_and_invalidate_helpers(self):
        helper = self._make_helper()
        with patch("venus_evcharger_auto_input_helper.time.time", side_effect=[100.0, 100.0]):
            self.assertTrue(helper._source_retry_ready("pv"))
            helper._delay_source_retry("pv")
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertFalse(helper._source_retry_ready("pv"))
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=105.0):
            self.assertTrue(helper._source_retry_ready("pv"))
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=0.5):
            self.assertTrue(helper._source_retry_ready("unknown"))

        helper = self._make_helper()
        helper.auto_dbus_backoff_base_seconds = 0.5
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=20.0):
            helper._delay_source_retry("grid")
        self.assertEqual(helper._source_retry_after["grid"], 21.0)

        helper = self._make_helper()
        helper.auto_dbus_backoff_base_seconds = 0.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=25.0):
            helper._delay_source_retry("grid")
        self.assertEqual(helper._source_retry_after["grid"], 30.0)

        helper = self._make_helper()
        helper.auto_dbus_backoff_base_seconds = 1.5
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=30.0):
            helper._delay_source_retry("battery")
        self.assertEqual(helper._source_retry_after["battery"], 31.5)

        helper._resolved_auto_pv_services = ["svc"]
        helper._auto_pv_last_scan = 100.0
        helper._invalidate_auto_pv_services()
        self.assertEqual(helper._resolved_auto_pv_services, [])
        self.assertEqual(helper._auto_pv_last_scan, 0.0)

        helper._resolved_auto_battery_service = "battery"
        helper._auto_battery_last_scan = 100.0
        helper._invalidate_auto_battery_service()
        self.assertIsNone(helper._resolved_auto_battery_service)
        self.assertEqual(helper._auto_battery_last_scan, 0.0)

    def test_resolve_auto_pv_services_uses_discovery_cache(self):
        helper = self._make_helper()
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.http_40",
                "com.victronenergy.pvinverter.http_41",
            ]
        )

        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", side_effect=[100.0, 120.0]):
            first = helper._resolve_auto_pv_services()
            second = helper._resolve_auto_pv_services()

        self.assertEqual(first, ["com.victronenergy.pvinverter.http_40", "com.victronenergy.pvinverter.http_41"])
        self.assertEqual(second, first)
        helper._list_dbus_services.assert_called_once_with()

    def test_resolve_auto_pv_services_refreshes_stale_cache_and_preserves_order_limit(self):
        helper = self._make_helper()
        helper._resolved_auto_pv_services = ["stale-pv"]
        helper._auto_pv_last_scan = 100.0
        helper.auto_pv_max_services = 1
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.http_2",
                "com.victronenergy.pvinverter.http_1",
            ]
        )

        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=200.0):
            self.assertEqual(helper._resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_2"])

        helper._list_dbus_services.assert_called_once_with()
        self.assertEqual(helper._resolved_auto_pv_services, ["com.victronenergy.pvinverter.http_2"])
        self.assertEqual(helper._auto_pv_last_scan, 200.0)

    def test_resolve_auto_pv_services_uses_configured_service_directly(self):
        helper = self._make_helper()
        helper.auto_pv_service = "com.victronenergy.pvinverter.http_40"
        self.assertEqual(helper._resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_40"])

    def test_get_pv_power_covers_retry_guard_and_read_failures(self):
        helper = self._make_helper()
        helper._source_retry_after["pv"] = 200.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_pv_power())

        helper = self._make_helper()
        helper.auto_use_dc_pv = True
        helper.auto_pv_service = ""
        helper._resolve_auto_pv_services = MagicMock(side_effect=RuntimeError("scan failed"))
        helper._get_dbus_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._delay_source_retry.assert_called_once_with("pv")

        helper = self._make_helper()
        helper.auto_use_dc_pv = True
        helper.auto_pv_service = "configured-pv"
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._delay_source_retry.assert_called_once_with("pv")

    def test_resolve_auto_battery_service_finds_prefixed_service_with_soc(self):
        helper = self._make_helper()
        helper.auto_battery_service = ""
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.battery.socketcan_can0",
                "com.victronenergy.battery.socketcan_can1",
            ]
        )
        helper._battery_service_has_soc = MagicMock(side_effect=lambda service_name: service_name.endswith("can1"))

        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "com.victronenergy.battery.socketcan_can1")

    def test_resolve_auto_battery_service_covers_configured_service_cache_and_failure(self):
        helper = self._make_helper()
        helper._list_dbus_services = MagicMock(return_value=[helper.auto_battery_service])
        helper._get_dbus_value = MagicMock(return_value=60.0)
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "com.victronenergy.battery.socketcan_can1")
        self.assertEqual(helper._resolved_auto_battery_service, "com.victronenergy.battery.socketcan_can1")

        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._list_dbus_services = MagicMock(return_value=["configured-battery"])
        helper._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
        helper._resolved_auto_battery_service = "cached-battery"
        helper._auto_battery_last_scan = 100.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=120.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "cached-battery")

        helper = self._make_helper()
        helper.auto_battery_service = ""
        helper._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        helper._battery_service_has_soc = MagicMock(return_value=False)
        with self.assertRaises(ValueError):
            helper._resolve_auto_battery_service()

    def test_battery_service_has_soc_and_get_battery_soc_cover_success_and_failure(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(side_effect=[55.0, RuntimeError("boom")])
        self.assertTrue(helper._battery_service_has_soc("svc"))
        self.assertFalse(helper._battery_service_has_soc("svc"))

        helper = self._make_helper()
        helper._resolve_auto_battery_service = MagicMock(return_value="battery")
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: 57.5})
        self.assertEqual(helper._get_battery_soc(), 57.5)

        helper = self._make_helper()
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: "bad"})
        self.assertIsNone(helper._get_battery_soc())

        helper = self._make_helper()
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: True})
        self.assertIsNone(helper._get_battery_soc())

        helper = self._make_helper()
        self._write_gateway_cache(helper, key_values={BATTERY_SOC_READ_KEY: 150.0})
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._delay_source_retry.assert_called_once_with("battery")

        helper = self._make_helper()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._delay_source_retry.assert_called_once_with("battery")

    def test_get_battery_soc_respects_source_retry_guard(self):
        helper = self._make_helper()
        helper._source_retry_after["battery"] = 200.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_battery_soc())
