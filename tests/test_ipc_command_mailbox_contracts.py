# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import venus_evcharger.ipc.command_mailbox as mailbox_module
import venus_evcharger.ipc.core_commands as core_commands_module
from venus_evcharger.core.contracts_control_surface import (
    CONTROL_AUTO_RUNTIME_TARGETS,
    CONTROL_CURRENT_SETTING_TARGETS,
)
from venus_evcharger.ipc.command_mailbox import (
    FileCommandMailbox,
    MailboxLockTimeout,
    MailboxScanUnavailable,
    MAX_MAILBOX_QUARANTINE_ENTRIES,
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
    CORE_COMMAND_RETRY_INITIAL_SECONDS,
    CORE_COMMAND_RETRY_MAX_SECONDS,
    CORE_COMMAND_QUEUE_CLASS,
    CORE_COMMAND_SCHEMA_VERSION,
    CoreCommandMailbox,
    CoreCommandQueuePolicy,
    core_command_retry_delay,
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
            with (
                patch.object(mailbox_module.Path, "glob", side_effect=OSError("unavailable")),
                patch.object(mailbox_module.logging, "warning") as warning,
            ):
                with self.assertRaises(MailboxScanUnavailable) as raised:
                    mailbox.load_pending()
            self.assertEqual(raised.exception.command_dir, mailbox.command_dir)
            self.assertEqual(str(raised.exception.error), "unavailable")
            self.assertEqual(
                str(raised.exception),
                f"Command mailbox scan unavailable path={mailbox.command_dir}: unavailable",
            )
            warning.assert_called_once_with(
                "Command mailbox directory scan failed path=%s error=%s",
                mailbox.command_dir,
                unittest.mock.ANY,
            )

            with patch.object(mailbox, "_locked", side_effect=PermissionError("read-only")):
                pending = mailbox.load_pending()
            self.assertEqual([path for path, _payload in pending], [second])

    def test_invalid_files_are_ram_quarantined_until_their_signature_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mailbox = CoreCommandMailbox(str(Path(temp_dir) / "commands"))
            command_dir = Path(mailbox.command_dir)
            command_dir.mkdir(parents=True)
            invalid_json = command_dir / "invalid.json"
            invalid_shape = command_dir / "list.json"
            invalid_json.write_text("{", encoding="utf-8")
            invalid_shape.write_text("[]", encoding="utf-8")

            with patch.object(mailbox_module.logging, "warning") as warning:
                self.assertEqual(mailbox.load_pending(), [])
                self.assertEqual(mailbox.load_pending(), [])
            self.assertEqual(warning.call_count, 2)
            self.assertEqual(
                tuple(Path(path).name for path in mailbox._quarantined),
                ("invalid.json", "list.json"),
            )

            invalid_json.write_text('{"value": 1}', encoding="utf-8")
            self.assertEqual(mailbox.load_pending(), [(str(invalid_json), {"value": 1})])
            self.assertEqual(tuple(Path(path).name for path in mailbox._quarantined), ("list.json",))
            invalid_shape.unlink()
            self.assertEqual(mailbox.load_pending(), [(str(invalid_json), {"value": 1})])
            self.assertEqual(mailbox._quarantined, {})

    def test_quarantine_and_directory_error_logging_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mailbox = CoreCommandMailbox(str(Path(temp_dir) / "commands"))
            command_dir = Path(mailbox.command_dir)
            command_dir.mkdir(parents=True)
            for index in range(3):
                (command_dir / f"bad-{index}.json").write_text("{", encoding="utf-8")

            with patch.object(mailbox_module, "MAX_MAILBOX_QUARANTINE_ENTRIES", 2):
                mailbox.load_pending()
            self.assertEqual(
                tuple(Path(path).name for path in mailbox._quarantined),
                ("bad-1.json", "bad-2.json"),
            )
            self.assertEqual(MAX_MAILBOX_QUARANTINE_ENTRIES, 128)

            with (
                patch.object(mailbox_module.Path, "glob", side_effect=OSError("scan failed")),
                patch.object(
                    mailbox_module.time,
                    "monotonic",
                    side_effect=(0.0, 0.0, 10.0, 10.0, 60.0, 60.0),
                ),
                patch.object(mailbox_module.logging, "warning") as warning,
            ):
                for _index in range(3):
                    with self.assertRaises(MailboxScanUnavailable):
                        mailbox.load_pending()
            self.assertEqual(warning.call_count, 2)

    def test_unreadable_file_is_observable_without_writing_a_quarantine_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mailbox = CoreCommandMailbox(str(Path(temp_dir) / "commands"))
            command_dir = Path(mailbox.command_dir)
            command_dir.mkdir(parents=True)
            command_path = command_dir / "denied.json"
            command_path.write_text("{}", encoding="utf-8")

            with (
                patch.object(mailbox_module, "open", side_effect=PermissionError("denied"), create=True),
                patch.object(mailbox_module.logging, "warning") as warning,
            ):
                self.assertEqual(mailbox.load_pending(), [])
            warning.assert_called_once_with(
                "Quarantined unreadable command file path=%s reason=%s",
                str(command_path),
                "PermissionError: denied",
            )
            self.assertEqual(
                {path.name for path in command_dir.iterdir()},
                {".mailbox.lock", command_path.name},
            )

    def test_directory_shape_and_stat_failures_are_observable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            not_directory = Path(temp_dir) / "commands"
            not_directory.write_text("not a directory", encoding="utf-8")
            mailbox = CoreCommandMailbox(str(not_directory))
            with patch.object(mailbox_module.logging, "warning") as warning:
                with self.assertRaises(MailboxScanUnavailable) as raised:
                    mailbox.load_pending()
            self.assertIsInstance(raised.exception.error, NotADirectoryError)
            warning.assert_called_once()

            missing = CoreCommandMailbox(str(Path(temp_dir) / "missing"))
            self.assertEqual(missing.load_pending(), [])

            denied = CoreCommandMailbox(str(Path(temp_dir) / "denied"))
            with (
                patch.object(mailbox_module.os, "stat", side_effect=PermissionError("blocked")),
                patch.object(mailbox_module.logging, "warning") as warning,
            ):
                with self.assertRaises(MailboxScanUnavailable) as raised:
                    denied.load_pending()
            self.assertIsInstance(raised.exception.error, PermissionError)
            warning.assert_called_once()

            locked = CoreCommandMailbox(temp_dir)
            with patch.object(locked, "_locked", side_effect=MailboxLockTimeout("busy")):
                with self.assertRaisesRegex(MailboxLockTimeout, "busy"):
                    locked.load_pending()
            with (
                patch.object(locked, "_locked", side_effect=OSError("lock unavailable")),
                patch.object(mailbox_module.logging, "warning") as warning,
            ):
                with self.assertRaises(MailboxScanUnavailable) as raised:
                    locked.load_pending()
            self.assertEqual(str(raised.exception.error), "lock unavailable")
            warning.assert_called_once_with(
                "Command mailbox directory scan failed path=%s error=%s",
                locked.command_dir,
                unittest.mock.ANY,
            )
            self.assertEqual(str(warning.call_args.args[2]), "lock unavailable")

    def test_directory_and_file_quarantine_helpers_preserve_exact_error_context(self) -> None:
        mailbox = CoreCommandMailbox("/tmp/commands")
        mailbox._quarantined["stale"] = mailbox_module._QuarantinedCommandFile(
            signature=(1, 2, 3),
            reason="old",
        )
        with patch.object(mailbox_module.os, "stat", side_effect=FileNotFoundError):
            self.assertFalse(mailbox._command_directory_available())
        self.assertEqual(mailbox._quarantined, {})

        denied = PermissionError("blocked")
        with (
            patch.object(mailbox_module.os, "stat", side_effect=denied),
            patch.object(mailbox, "_record_directory_error") as record_error,
        ):
            with self.assertRaises(MailboxScanUnavailable) as raised:
                mailbox._command_directory_available()
        self.assertIs(raised.exception.error, denied)
        record_error.assert_called_once_with(denied)

        regular_file = MagicMock(st_mode=0o100600)
        with (
            patch.object(mailbox_module.os, "stat", return_value=regular_file),
            patch.object(mailbox, "_record_directory_error") as record_error,
        ):
            with self.assertRaises(MailboxScanUnavailable) as raised:
                mailbox._command_directory_available()
        shape_error = raised.exception.error
        self.assertIsInstance(shape_error, NotADirectoryError)
        self.assertEqual(shape_error.args, (mailbox.command_dir,))
        record_error.assert_called_once_with(shape_error)

        scan_error = OSError("scan")
        with (
            patch.object(mailbox_module.Path, "glob", side_effect=scan_error),
            patch.object(mailbox, "_record_directory_error") as record_error,
        ):
            with self.assertRaises(MailboxScanUnavailable) as raised:
                mailbox._load_pending_unlocked()
        self.assertIs(raised.exception.error, scan_error)
        record_error.assert_called_once_with(scan_error)

    def test_load_command_file_passes_exact_generation_and_reason_to_quarantine(self) -> None:
        mailbox = CoreCommandMailbox("/tmp/commands")
        path = Path("/tmp/commands/item.json")
        signature = (11, 22, 33)
        with (
            patch.object(mailbox_module, "_command_file_signature", return_value=signature) as file_signature,
            patch.object(mailbox, "_is_unchanged_quarantined", return_value=False) as unchanged,
            patch.object(mailbox_module, "_read_command_json", return_value=[]) as read_json,
            patch.object(mailbox, "_quarantine") as quarantine,
        ):
            self.assertIsNone(mailbox._load_command_file(path))
        file_signature.assert_called_once_with(str(path))
        unchanged.assert_called_once_with(str(path), signature)
        read_json.assert_called_once_with(str(path))
        quarantine.assert_called_once_with(str(path), signature, "payload-is-not-object")

        read_error = PermissionError("denied")
        with (
            patch.object(mailbox_module, "_command_file_signature", return_value=signature),
            patch.object(mailbox, "_is_unchanged_quarantined", return_value=False),
            patch.object(mailbox_module, "_read_command_json", side_effect=read_error),
            patch.object(mailbox, "_quarantine") as quarantine,
        ):
            self.assertIsNone(mailbox._load_command_file(path))
        quarantine.assert_called_once_with(str(path), signature, "PermissionError: denied")

    def test_quarantine_generation_matching_and_fifo_bound_are_exact(self) -> None:
        mailbox = CoreCommandMailbox("/tmp/commands")
        signature = (1, 2, 3)
        self.assertFalse(mailbox._is_unchanged_quarantined("/tmp/a.json", signature))
        with patch.object(mailbox_module.logging, "warning"):
            mailbox._quarantine("/tmp/a.json", signature, "invalid-a")
        self.assertTrue(mailbox._is_unchanged_quarantined("/tmp/a.json", signature))
        self.assertFalse(mailbox._is_unchanged_quarantined("/tmp/a.json", (1, 2, 4)))
        entry = mailbox._quarantined["/tmp/a.json"]
        self.assertEqual(entry.signature, signature)
        self.assertEqual(entry.reason, "invalid-a")

        with (
            patch.object(mailbox_module, "MAX_MAILBOX_QUARANTINE_ENTRIES", 2),
            patch.object(mailbox_module.logging, "warning"),
        ):
            mailbox._quarantine("/tmp/b.json", (2, 2, 2), "invalid-b")
            mailbox._quarantine("/tmp/c.json", (3, 3, 3), "invalid-c")
        self.assertEqual(tuple(mailbox._quarantined), ("/tmp/b.json", "/tmp/c.json"))

    def test_directory_error_logging_uses_exact_interval_and_error(self) -> None:
        mailbox = CoreCommandMailbox("/tmp/commands")
        first = OSError("first")
        second = OSError("second")
        third = OSError("third")
        with (
            patch.object(mailbox_module.time, "monotonic", side_effect=(40.0, 50.0, 100.0)),
            patch.object(mailbox_module.logging, "warning") as warning,
        ):
            mailbox._record_directory_error(first)
            mailbox._record_directory_error(second)
            mailbox._record_directory_error(third)
        self.assertEqual(
            warning.call_args_list,
            [
                unittest.mock.call(
                    "Command mailbox directory scan failed path=%s error=%s",
                    mailbox.command_dir,
                    first,
                ),
                unittest.mock.call(
                    "Command mailbox directory scan failed path=%s error=%s",
                    mailbox.command_dir,
                    third,
                ),
            ],
        )
        self.assertEqual(mailbox._last_directory_error_log_at, 100.0)

    def test_quarantine_deduplicates_identical_events_and_stat_failure_has_no_signature(self) -> None:
        mailbox = CoreCommandMailbox("/tmp/not-used")
        signature = (1, 2, 3)
        with patch.object(mailbox_module.logging, "warning") as warning:
            mailbox._quarantine("/tmp/bad.json", signature, "invalid")
            mailbox._quarantine("/tmp/bad.json", signature, "invalid")
        self.assertEqual(len(mailbox._quarantined), 1)
        warning.assert_called_once()
        with patch.object(mailbox_module.os, "stat", side_effect=OSError("gone")):
            self.assertIsNone(mailbox_module._command_file_signature("/tmp/bad.json"))

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
        self.assertTrue(mailbox_module._same_mailbox_revision({"value": 1}, {"value": 1}))
        self.assertFalse(mailbox_module._same_mailbox_revision({"value": 2}, {"value": 1}))

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

    def test_advisory_lock_retries_once_and_times_out_at_the_deadline(self) -> None:
        with (
            patch.object(
                mailbox_module.fcntl,
                "flock",
                side_effect=(BlockingIOError("busy"), None),
            ) as flock,
            patch.object(mailbox_module.time, "monotonic", side_effect=(10.0, 10.25)),
            patch.object(mailbox_module.time, "sleep") as sleep,
        ):
            mailbox_module._acquire_lock(7, 1.0)
        self.assertEqual(flock.call_count, 2)
        sleep.assert_called_once_with(mailbox_module.MAILBOX_LOCK_RETRY_SECONDS)

        timeout_error = BlockingIOError("still busy")
        with (
            patch.object(mailbox_module.fcntl, "flock", side_effect=timeout_error),
            patch.object(mailbox_module.time, "monotonic", side_effect=(20.0, 20.25)),
            patch.object(mailbox_module.time, "sleep") as sleep,
            self.assertRaisesRegex(MailboxLockTimeout, "Command mailbox lock timed out"),
        ):
            mailbox_module._acquire_lock(8, 0.25)
        sleep.assert_not_called()

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
        self.assertEqual(core_command_retry_delay(-5), CORE_COMMAND_RETRY_INITIAL_SECONDS)
        self.assertEqual(core_command_retry_delay(1), CORE_COMMAND_RETRY_INITIAL_SECONDS)
        self.assertEqual(core_command_retry_delay(2), 1.0)
        self.assertEqual(core_command_retry_delay(6), 16.0)
        self.assertEqual(core_command_retry_delay(7), CORE_COMMAND_RETRY_MAX_SECONDS)
        self.assertEqual(core_command_retry_delay(1000), CORE_COMMAND_RETRY_MAX_SECONDS)
        auto_target = min(CONTROL_AUTO_RUNTIME_TARGETS)
        current_target = min(CONTROL_CURRENT_SETTING_TARGETS)
        self.assertEqual(
            core_control_command_payload(
                "set_auto_runtime_setting",
                auto_target,
                1,
                source="control-surface",
                origin="test",
            )["target"],
            auto_target,
        )
        self.assertEqual(
            core_control_command_payload(
                "set_current_setting",
                current_target,
                6.0,
                source="control-surface",
                origin="test",
            )["target"],
            current_target,
        )
        with self.assertRaisesRegex(ValueError, "Unsupported core control route"):
            core_control_command_payload(
                "set_auto_runtime_setting",
                current_target,
                1,
                source="control-surface",
                origin="test",
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
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError) as raised:
                core_control_command_payload(value=1, **kwargs)
            self.assertEqual(str(raised.exception), message)

    def test_core_command_private_validation_helpers_reject_each_untrusted_text_field(self) -> None:
        fields = {
            "kind": "user_command",
            "target": "mode",
            "origin": "gateway-gui",
            "id": "command-1",
        }
        self.assertEqual(
            core_commands_module._core_control_text_fields(fields),
            ("user_command", "mode", "gateway-gui", "command-1"),
        )
        for name in fields:
            with self.subTest(field=name):
                invalid = dict(fields)
                invalid[name] = ""
                self.assertIsNone(core_commands_module._core_control_text_fields(invalid))
        self.assertFalse(core_commands_module._is_control_command_name("unsupported"))
        self.assertFalse(core_commands_module._is_control_command_name(7))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
