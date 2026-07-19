# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter introspection request-file and enqueue contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    builtins,
    gateway_paths,
    install_mock,
    introspection_module,
    json,
    patch,
    tempfile,
    time,
)


class GatewayIntrospectionRequestCases(GatewayAdapterContractCase):
    """Exercise introspection request-file and enqueue contracts."""

    def test_gateway_processes_legacy_introspection_request_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionRequestPath={request_path}\n"
                f"DbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            request_path.write_text(
                json.dumps(
                    {
                        "requests": [
                            {
                                "service": "com.victronenergy.system",
                                "path": "/Ac/Grid/L1/Power",
                                "priority": 100,
                                "source": "test",
                                "reason": "unit",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter.process_introspection_requests_once()

            pending = adapter.commands.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["kind"], "introspect")
            self.assertEqual(pending[0][1]["coalesce_key"], "introspect:com.victronenergy.system:/Ac/Grid/L1/Power")
            self.assertEqual(json.loads(request_path.read_text(encoding="utf-8")), {"requests": []})

    def test_gateway_introspection_request_and_background_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                f"[DEFAULT]\nDbusIntrospectionRequestPath={request_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_enabled = False
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])

            adapter.dbus_introspection_enabled = True
            adapter.dbus_introspection_request_path = ""
            self.assertEqual(adapter.introspection_request_payload(), {})
            adapter.dbus_introspection_request_path = str(request_path)
            request_path.write_text("[]", encoding="utf-8")
            self.assertEqual(adapter.introspection_request_payload(), {})
            request_path.write_text("{", encoding="utf-8")
            self.assertEqual(adapter.introspection_request_payload(), {})
            request_path.write_text(json.dumps({"requests": "bad"}), encoding="utf-8")
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])
            request_path.write_text(
                json.dumps({"requests": ["bad", {}, {"service": "", "path": "/P"}]}), encoding="utf-8"
            )
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])
            request_path.write_text(json.dumps({"requests": [{"service": "svc", "path": "/P"}]}), encoding="utf-8")
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter._introspection_queue_depth, 1)
            clear_error = RuntimeError("readonly")
            with (
                patch.object(
                    introspection_module,
                    "write_text_atomically",
                    MagicMock(side_effect=clear_error),
                ),
                patch.object(introspection_module.logging, "debug") as debug,
            ):
                adapter.clear_introspection_request_payload()
            debug.assert_called_once_with(
                "Unable to clear DBus introspection request payload %s: %s",
                str(request_path),
                clear_error,
            )

            adapter._introspection_queue_depth = 5
            request_path.write_text(
                json.dumps({"requests": [{"service": "svc", "path": "/A"}, {"service": "svc", "path": "/B"}]}),
                encoding="utf-8",
            )
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter._introspection_queue_depth, 7)

            background = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-bg")))
            install_mock(background, "enqueue_introspection_command", MagicMock())
            background.enqueue_background_introspection_if_due()
            background.enqueue_introspection_command.assert_not_called()
            background.cache.update_services(["com.victronenergy.battery.tty1", "com.victronenergy.pvinverter.http_1"])
            background.circuit.protective_until = time.time() + 10.0
            background.enqueue_background_introspection_if_due()
            background.enqueue_introspection_command.assert_not_called()
            background.circuit.protective_until = 0.0
            background._last_introspection_full_scan_at = 0.0
            background.enqueue_background_introspection_if_due()
            self.assertGreater(background.enqueue_introspection_command.call_count, 0)
            self.assertGreater(background._last_introspection_full_scan_at, 0.0)
            background.enqueue_introspection_command.assert_any_call(
                "com.victronenergy.battery.tty1",
                "/Soc",
                priority=70,
                source="battery",
                reason="battery-service-discovery",
            )
            self.assertEqual(
                background.configured_or_prefixed_services(
                    "UnusedExplicit",
                    "UnusedPrefix",
                    "com.victronenergy.pvinverter",
                ),
                ["com.victronenergy.pvinverter.http_1"],
            )
            background.config["DEFAULT"]["UnusedExplicit"] = "com.victronenergy.pvinverter.missing"
            self.assertEqual(
                background.configured_or_prefixed_services(
                    "UnusedExplicit",
                    "UnusedPrefix",
                    "com.victronenergy.pvinverter",
                ),
                [],
            )

            quiet_config = Path(temp_dir) / "quiet.ini"
            quiet_config.write_text(
                "[DEFAULT]\n"
                "AutoGridService=\n"
                "AutoGridL1Path=\n"
                "AutoGridL2Path=\n"
                "AutoGridL3Path=\n"
                "AutoBatterySocPath=\n"
                "AutoPvPath=\n",
                encoding="utf-8",
            )
            quiet_background = DbusAdapter(str(quiet_config), paths=gateway_paths(str(Path(temp_dir) / "run-quiet-bg")))
            quiet_background.cache.update_services(
                ["com.victronenergy.battery.tty1", "com.victronenergy.pvinverter.http_1"]
            )
            self.assertEqual(quiet_background.background_introspection_specs(), [])

    def test_gateway_introspection_request_contracts(self) -> None:
        payload = {
            "requests": [
                "bad",
                {},
                {"service": "svc-missing-path"},
                {"path": "/MissingService"},
                {"service": "", "path": "/Missing"},
                {"service": " svc ", "path": " /Path ", "priority": "88.9", "source": "", "reason": ""},
                {"service": "svc-defaults", "path": "/Defaults"},
                {"service": "svc2", "path": "/P2", "priority": "bad", "source": "api", "reason": "need"},
            ]
        }

        self.assertEqual(
            introspection_module._valid_introspection_requests(payload),
            [
                {"service": "svc", "path": "/Path", "priority": 88, "source": "request", "reason": "requested"},
                {
                    "service": "svc-defaults",
                    "path": "/Defaults",
                    "priority": 100,
                    "source": "request",
                    "reason": "requested",
                },
                {"service": "svc2", "path": "/P2", "priority": 100, "source": "api", "reason": "need"},
            ],
        )
        self.assertEqual(introspection_module._valid_introspection_requests({"requests": "bad"}), [])
        self.assertEqual(introspection_module._int_or_default(None, 7), 7)
        self.assertEqual(introspection_module._drop_command({"kind": "unknown"}), "dropped")

    def test_gateway_introspection_file_payload_uses_utf8_and_dict_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            request_path.write_text(json.dumps({"requests": []}), encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_request_path = str(request_path)

            with patch.object(builtins, "open", wraps=builtins.open) as open_mock:
                self.assertEqual(adapter.introspection_request_payload(), {"requests": []})
            open_mock.assert_called_once_with(str(request_path), encoding="utf-8")

            request_path.write_bytes(b"\xff")
            self.assertEqual(adapter.introspection_request_payload(), {})

    def test_gateway_enqueue_introspection_requests_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            enqueue = install_mock(adapter, "enqueue_introspection_command", MagicMock())

            accepted = adapter.enqueue_introspection_requests(
                {
                    "requests": [
                        {"service": "svc", "path": "/A", "priority": 11, "source": "src-a", "reason": "why-a"},
                        {"service": "svc", "path": "/B", "priority": 22, "source": "src-b", "reason": "why-b"},
                    ]
                }
            )

            self.assertEqual(accepted, 2)
            self.assertEqual(
                enqueue.call_args_list[0].args,
                ("svc", "/A"),
            )
            self.assertEqual(
                enqueue.call_args_list[0].kwargs,
                {"priority": 11, "source": "src-a", "reason": "why-a"},
            )
            self.assertEqual(enqueue.call_args_list[1].args, ("svc", "/B"))
            self.assertEqual(
                enqueue.call_args_list[1].kwargs,
                {"priority": 22, "source": "src-b", "reason": "why-b"},
            )

    def test_gateway_introspection_enqueue_command_payload_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusIntrospectionTimeoutSeconds=2.5\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            enqueue = install_mock(adapter.commands, "enqueue", MagicMock())

            adapter.enqueue_introspection_command("svc", "/Low", priority=89, source="test", reason="low")
            adapter.enqueue_introspection_command("svc", "/High", priority=90, source="test", reason="high")

            self.assertEqual(
                enqueue.call_args_list[0].args[0],
                {
                    "kind": "introspect",
                    "service": "svc",
                    "path": "/Low",
                    "priority": "discovery",
                    "source": "test",
                    "reason": "low",
                    "timeout": 2.5,
                    "coalesce_key": "introspect:svc:/Low",
                },
            )
            self.assertEqual(enqueue.call_args_list[1].args[0]["priority"], "optional")
            self.assertEqual(enqueue.call_args_list[1].args[0]["coalesce_key"], "introspect:svc:/High")

            default_config = Path(temp_dir) / "default.ini"
            default_config.write_text("[DEFAULT]\n", encoding="utf-8")
            default_adapter = DbusAdapter(str(default_config), paths=gateway_paths(str(Path(temp_dir) / "run-default")))
            default_enqueue = install_mock(default_adapter.commands, "enqueue", MagicMock())
            default_adapter.enqueue_introspection_command(
                "svc", "/Default", priority=90, source="test", reason="default"
            )
            self.assertEqual(default_enqueue.call_args.args[0]["timeout"], 1.0)
