# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

import venus_evcharger.ipc.command_mailbox as mailbox_module
from venus_evcharger.ipc.command_mailbox import (
    FileCommandMailbox,
    command_file_id,
    command_float,
    command_priority_rank,
    coalesced_target_exists,
    mark_coalesced_payload,
    normalized_mapping,
    read_command_json,
    same_command_priority,
    select_coalesced_command,
    should_replace_command,
    write_command_json,
)
from venus_evcharger.ipc.core_commands import (
    CORE_COMMAND_QUEUE_CLASS,
    CORE_COMMAND_SCHEMA_VERSION,
    CoreCommandMailbox,
    CoreCommandQueuePolicy,
    core_control_command_payload,
    parse_core_control_command,
)


class IpcCommandMailboxContractTests(unittest.TestCase):
    def test_neutral_mailbox_delegates_queue_semantics_to_its_policy(self) -> None:
        class RecordingPolicy(CoreCommandQueuePolicy):
            schema_version = 7

            @staticmethod
            def queue_class(command: object) -> str:
                assert isinstance(command, dict)
                return str(command["target_queue"])

            @staticmethod
            def merge_coalesced(existing: object, payload: object) -> None:
                assert isinstance(existing, dict)
                assert isinstance(payload, dict)
                payload["previous_value"] = existing["value"]

        with tempfile.TemporaryDirectory() as temp_dir:
            mailbox = FileCommandMailbox(str(Path(temp_dir) / "commands"), policy=RecordingPolicy())
            first = mailbox.enqueue(
                {
                    "coalesce_key": "setting",
                    "created_at": 1.0,
                    "priority": "user",
                    "target_queue": "control",
                    "value": 1,
                }
            )
            mailbox.enqueue(
                {
                    "coalesce_key": "setting",
                    "created_at": 2.0,
                    "priority": "user",
                    "target_queue": "control",
                    "value": 2,
                }
            )

            payload = read_command_json(first)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["schema_version"], 7)
            self.assertEqual(payload["queue_class"], "control")
            self.assertEqual(payload["previous_value"], 1)
            self.assertEqual(payload["value"], 2)

    def test_core_mailbox_builds_and_coalesces_atomic_envelopes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mailbox = CoreCommandMailbox(str(Path(temp_dir) / "core-commands"))
            command = core_control_command_payload(
                "set_mode",
                "mode",
                1,
                source="control-surface",
                origin=" callback ",
            )
            with patch.object(mailbox_module, "_now", side_effect=(10.0, 20.0, 21.0)):
                first_path = mailbox.enqueue(command)
                second_path = mailbox.enqueue({**command, "value": 2})

            self.assertEqual(first_path, second_path)
            payload = read_command_json(first_path)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["schema_version"], CORE_COMMAND_SCHEMA_VERSION)
            self.assertEqual(payload["queue_class"], CORE_COMMAND_QUEUE_CLASS)
            self.assertEqual(payload["kind"], "user_command")
            self.assertEqual(payload["source"], "control-surface")
            self.assertEqual(payload["origin"], "callback")
            self.assertEqual(payload["value"], 2)
            self.assertEqual(payload["created_at"], 10.0)
            self.assertEqual(payload["updated_at"], 21.0)
            self.assertEqual(payload["lifecycle_state"], "coalesced")
            self.assertTrue(str(payload["id"]).startswith("coalesced-"))

            lower_priority = {**command, "value": 3, "priority": "diagnostic", "created_at": 30.0}
            self.assertEqual(mailbox.enqueue(lower_priority), first_path)
            self.assertEqual(read_command_json(first_path), payload)

            higher_priority = {**command, "value": 4, "priority": "safety", "created_at": 5.0}
            self.assertEqual(mailbox.enqueue(higher_priority), first_path)
            replaced = read_command_json(first_path)
            self.assertIsInstance(replaced, dict)
            assert isinstance(replaced, dict)
            self.assertEqual(replaced["value"], 4)
            self.assertEqual(replaced["created_at"], 5.0)
            self.assertNotIn("updated_at", replaced)

            unkeyed_path = mailbox.enqueue({"kind": "maintenance", "created_at": 40.0})
            self.assertNotEqual(unkeyed_path, first_path)
            unkeyed_payload = read_command_json(unkeyed_path)
            self.assertIsInstance(unkeyed_payload, dict)
            assert isinstance(unkeyed_payload, dict)
            self.assertEqual(unkeyed_payload["lifecycle_state"], "queued")
            self.assertEqual(mailbox.remove_coalesced(""), 0)
            self.assertEqual(mailbox.remove_coalesced("missing"), 0)
            self.assertTrue(Path(unkeyed_path).exists())
            self.assertEqual(mailbox.remove_coalesced("core:set_mode:mode"), 1)
            self.assertFalse(Path(first_path).exists())
            self.assertTrue(Path(unkeyed_path).exists())

    def test_mailbox_load_remove_and_coalesce_are_resilient(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mailbox = CoreCommandMailbox(str(Path(temp_dir) / "commands"))
            command_dir = Path(mailbox.command_dir)
            command_dir.mkdir(parents=True)
            (command_dir / "bad.json").write_text("{", encoding="utf-8")
            (command_dir / "list.json").write_text("[]", encoding="utf-8")
            first = mailbox.enqueue({"id": "first", "priority": "normal", "created_at": 2.0})
            second = mailbox.enqueue({"id": "second", "priority": "user", "created_at": 3.0})

            self.assertEqual([path for path, _payload in mailbox.load_pending()], [first, second])
            commands = [
                ("passthrough", {"id": "pass", "priority": "diagnostic", "created_at": "bad"}),
                ("passthrough-first", {"id": "pass-first", "priority": "normal", "created_at": 1.0}),
                ("old", {"id": "old", "priority": "normal", "created_at": 20.0, "coalesce_key": "same"}),
                ("important", {"id": "important", "priority": "user", "created_at": 10.0, "coalesce_key": "same"}),
                ("late-low", {"id": "late-low", "priority": "normal", "created_at": 30.0, "coalesce_key": "same"}),
            ]
            self.assertEqual(
                [path for path, _payload in mailbox.coalesce(commands)],
                ["important", "passthrough-first", "passthrough"],
            )

            mailbox.remove(str(command_dir / "missing.json"))
            mailbox.remove(first)
            self.assertFalse(Path(first).exists())
            with patch.object(mailbox_module.Path, "glob", side_effect=OSError("unavailable")):
                self.assertEqual(mailbox.load_pending(), [])

    def test_transport_helpers_define_priority_and_latest_wins_semantics(self) -> None:
        self.assertEqual(command_priority_rank(" SAFETY "), 0)
        self.assertEqual(command_priority_rank("unknown"), 6)
        self.assertEqual(command_priority_rank(None), 6)
        self.assertEqual(command_priority_rank(""), 6)
        self.assertEqual(command_priority_rank("   "), 6)
        self.assertEqual(command_priority_rank("unknown"), command_priority_rank(None))
        self.assertEqual(command_float("3.5"), 3.5)
        self.assertEqual(command_float("bad"), 0.0)
        self.assertEqual(command_float(object()), 0.0)

        deterministic = command_file_id({"coalesce_key": "core:set_mode:mode"})
        self.assertEqual(
            deterministic,
            command_file_id({"coalesce_key": "core:set_mode:mode"}),
        )
        self.assertTrue(deterministic.startswith("coalesced-"))
        self.assertEqual(len(deterministic.removeprefix("coalesced-")), 24)
        random_id = command_file_id({})
        self.assertTrue(random_id.startswith("cmd-"))
        self.assertEqual(len(random_id.rsplit("-", 1)[1]), 8)

        diagnostic = {"priority": "diagnostic", "created_at": 20.0}
        user = {"priority": "user", "created_at": 10.0}
        self.assertTrue(should_replace_command(diagnostic, user))
        self.assertFalse(should_replace_command(user, diagnostic))
        self.assertFalse(should_replace_command({"priority": "user", "created_at": 20.0}, user))
        self.assertTrue(should_replace_command(user, {"priority": "user", "created_at": 10.0}))
        candidate = ("new", user)
        self.assertIs(select_coalesced_command(None, candidate), candidate)
        self.assertEqual(select_coalesced_command(("old", diagnostic), candidate)[0], "new")
        self.assertEqual(select_coalesced_command(("old", user), ("new", diagnostic))[0], "old")

        with tempfile.TemporaryDirectory() as temp_dir:
            target = str(Path(temp_dir) / "command.json")
            Path(target).write_text("{}", encoding="utf-8")
            self.assertFalse(coalesced_target_exists({}, target))
            self.assertTrue(coalesced_target_exists({"coalesce_key": " key "}, target))

        self.assertTrue(same_command_priority(user, {"priority": " USER "}))
        self.assertFalse(same_command_priority(user, diagnostic))
        payload = {"priority": "user", "created_at": 20.0}
        mark_coalesced_payload(None, payload)
        self.assertEqual(payload["lifecycle_state"], "coalesced")
        self.assertNotIn("updated_at", payload)
        payload = {"priority": "user", "created_at": 20.0}
        with patch.object(mailbox_module, "_now", return_value=30.0):
            mark_coalesced_payload({"priority": "user", "created_at": 10.0}, payload)
        self.assertEqual(payload["created_at"], 10.0)
        self.assertEqual(payload["updated_at"], 30.0)
        payload = {"priority": "user", "created_at": 22.0}
        mark_coalesced_payload({"priority": "user", "created_at": 0.0}, payload)
        self.assertEqual(payload["created_at"], 22.0)

    def test_json_transport_normalizes_untrusted_values(self) -> None:
        self.assertIsNone(normalized_mapping([]))
        self.assertEqual(normalized_mapping({1: "value"}), {"1": "value"})
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "payload.json")
            write_command_json(
                path,
                {
                    "object": object(),
                    "mapping": {1: True},
                    "sequence": (None, 2.5),
                },
            )
            payload = read_command_json(path)
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertIn("object object", payload["object"])
            self.assertEqual(payload["mapping"], {"1": True})
            self.assertEqual(payload["sequence"], [None, 2.5])
            Path(path).write_text("{", encoding="utf-8")
            self.assertIsNone(read_command_json(path))
            self.assertIsNone(read_command_json(str(Path(temp_dir) / "missing.json")))

        opener = mock_open(read_data='{"value": 1}')
        with patch.object(mailbox_module, "open", opener, create=True):
            self.assertEqual(read_command_json("command.json"), {"value": 1})
        opener.assert_called_once_with("command.json", encoding="utf-8")

    def test_core_command_contract_accepts_only_complete_transport_envelopes(self) -> None:
        policy = CoreCommandQueuePolicy()
        self.assertEqual(
            policy.normalize({"name": "set_mode", "target": "mode"}),
            {"name": "set_mode", "target": "mode"},
        )
        self.assertEqual(policy.queue_class({}), CORE_COMMAND_QUEUE_CLASS)
        payload = {"value": 1}
        policy.merge_coalesced({"value": 0}, payload)
        self.assertEqual(payload, {"value": 1})
        self.assertEqual(
            policy.order_key({"priority": "user", "created_at": 2.0, "id": "a"}),
            (1, 0, 2.0, 0, "a"),
        )
        self.assertEqual(policy.order_key({}), (6, 0, 0.0, 0, ""))
        self.assertLess(
            policy.order_key({"priority": "user", "created_at": 2.0, "id": "a"}),
            policy.order_key({"priority": "normal", "created_at": 1.0, "id": "b"}),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            mailbox = CoreCommandMailbox(str(Path(temp_dir) / "commands"))
            path = mailbox.enqueue(
                core_control_command_payload(
                    "set_mode",
                    " mode ",
                    2,
                    source="control-surface",
                    origin="callback",
                )
            )
            envelope = mailbox.load_pending()[0][1]
            parsed = parse_core_control_command(envelope)
            self.assertIsNotNone(parsed)
            assert parsed is not None
            self.assertEqual(parsed.name, "set_mode")
            self.assertEqual(parsed.target, "mode")
            self.assertEqual(parsed.value, 2)
            self.assertEqual(parsed.source, "control-surface")
            self.assertEqual(parsed.origin, "callback")
            self.assertEqual(parsed.command_id, Path(path).stem)
            self.assertGreater(parsed.created_at, 0.0)

        self.assertEqual(
            core_control_command_payload(
                "set_mode",
                " mode ",
                2,
                source="control-surface",
                origin=" callback ",
            ),
            {
                "kind": "user_command",
                "name": "set_mode",
                "target": "mode",
                "source": "control-surface",
                "origin": "callback",
                "value": 2,
                "priority": "user",
                "coalesce_key": "core:set_mode:mode",
            },
        )

        invalid_overrides = (
            {"schema_version": True},
            {"schema_version": 2},
            {"queue_class": "dbus"},
            {"kind": "other"},
            {"name": "set_enable"},
            {"target": "enable"},
            {"priority": "diagnostic"},
            {"coalesce_key": "core:set_mode:other"},
            {"id": ""},
            {"source": ""},
            {"source": "gateway-gui"},
            {"origin": ""},
            {"created_at": 0.0},
            {"created_at": float("inf")},
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                self.assertIsNone(parse_core_control_command({**envelope, **override}))

        for missing_field in (
            "schema_version",
            "queue_class",
            "kind",
            "name",
            "target",
            "priority",
            "coalesce_key",
            "id",
            "source",
            "origin",
            "created_at",
        ):
            with self.subTest(missing_field=missing_field):
                incomplete = dict(envelope)
                incomplete.pop(missing_field)
                self.assertIsNone(parse_core_control_command(incomplete))

        legacy_kind = dict(envelope)
        legacy_kind["type"] = legacy_kind.pop("kind")
        self.assertIsNone(parse_core_control_command(legacy_kind))
        self.assertIsNone(parse_core_control_command({**envelope, "priority": "USER"}))
        self.assertIsNone(parse_core_control_command({**envelope, "origin": 1}))
        self.assertIsNone(parse_core_control_command({**envelope, "id": 1}))
        self.assertIsNotNone(parse_core_control_command({**envelope, "created_at": 0.5}))

        invalid_payload_arguments = (
            (
                {
                    "name": "set_mode",
                    "target": " ",
                    "source": "control-surface",
                    "origin": "callback",
                },
                "Core control target must not be empty",
            ),
            (
                {
                    "name": "set_mode",
                    "target": "mode",
                    "source": "",
                    "origin": "callback",
                },
                "Core control source must not be empty",
            ),
            (
                {
                    "name": "set_mode",
                    "target": "mode",
                    "source": "control-surface",
                    "origin": "",
                },
                "Core control origin must not be empty",
            ),
        )
        for kwargs, message in invalid_payload_arguments:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                core_control_command_payload(value=1, **kwargs)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
