# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.control import LocalControlApiClient

from tests.venus_evcharger_control_test_support import started_control_api_server


class TestVenusEvchargerControlSmoke(unittest.TestCase):
    def test_live_tcp_smoke_covers_state_command_events_and_concurrency(self) -> None:
        with started_control_api_server() as (_service, server):
            read_client = LocalControlApiClient(
                base_url=f"http://{server.bound_host}:{server.bound_port}",
                bearer_token="read-token",
            )
            control_client = LocalControlApiClient(
                base_url=f"http://{server.bound_host}:{server.bound_port}",
                bearer_token="control-token",
            )

            summary = read_client.state("summary")
            self.assertEqual(summary.status, 200)
            self.assertEqual(summary.json()["kind"], "summary")
            state_token = summary.headers["X-State-Token"]
            self.assertEqual(summary.headers["ETag"], f'"{state_token}"')

            command = control_client.command(
                {"name": "set_mode", "value": 1},
                command_id="smoke-mode-1",
                idempotency_key="smoke-mode-1",
                if_match=state_token,
            )
            self.assertEqual(command.status, 200)
            self.assertEqual(command.json()["result"]["status"], "applied")

            stale = control_client.command(
                {"name": "set_mode", "value": 2},
                command_id="smoke-mode-2",
                idempotency_key="smoke-mode-2",
                if_match=state_token,
            )
            self.assertEqual(stale.status, 409)
            self.assertEqual(stale.json()["error"]["code"], "conflict")

            events = read_client.events(kinds=("command",), once=True)
            self.assertEqual(events.status, 200)
            command_events = events.ndjson()
            self.assertTrue(command_events)
            self.assertTrue(all(event["kind"] == "command" for event in command_events))
            self.assertEqual(command_events[-1]["payload"]["command"]["name"], "set_mode")

            heartbeat = read_client.events(after=999, timeout=0.05, heartbeat=0.01, kinds=("heartbeat",))
            self.assertEqual(heartbeat.status, 200)
            self.assertIn("X-Control-Api-Retry-Ms", heartbeat.headers)
            heartbeat_events = heartbeat.ndjson()
            self.assertTrue(heartbeat_events)
            self.assertTrue(all(event["kind"] == "heartbeat" for event in heartbeat_events))
            self.assertIn("retry_hint_ms", heartbeat_events[-1]["payload"])
            self.assertIn("resume_hint", heartbeat_events[-1]["payload"])

    def test_live_unix_socket_smoke_covers_read_and_write_paths(self) -> None:
        with started_control_api_server(unix_socket=True) as (_service, server):
            read_client = LocalControlApiClient(
                unix_socket_path=server.bound_unix_socket_path,
                bearer_token="read-token",
            )
            control_client = LocalControlApiClient(
                unix_socket_path=server.bound_unix_socket_path,
                bearer_token="control-token",
            )

            capabilities = read_client.capabilities()
            self.assertEqual(capabilities.status, 200)
            self.assertEqual(capabilities.json()["api_version"], "v1")

            command = control_client.command(
                {
                    "name": "set_current_setting",
                    "target": "set_current",
                    "value": 12.5,
                },
                command_id="unix-current",
                idempotency_key="unix-current",
            )
            self.assertEqual(command.status, 200)
            self.assertEqual(command.json()["result"]["status"], "applied")

            runtime = read_client.state("runtime")
            self.assertEqual(runtime.status, 200)
            self.assertEqual(runtime.json()["state"]["current_setting"], 12.5)


if __name__ == "__main__":
    unittest.main()
