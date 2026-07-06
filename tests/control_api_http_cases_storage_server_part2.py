# SPDX-License-Identifier: GPL-3.0-or-later
import json
from tempfile import TemporaryDirectory
from unittest.mock import mock_open, patch

from venus_evcharger.control import ControlApiIdempotencyStore


class _ControlApiHttpStorageServerCasesPart2:
    def test_idempotency_store_persists_only_to_runtime_paths_and_survives_restart(self) -> None:
        with (
            patch("venus_evcharger.control.idempotency.open", create=True) as open_mock,
            patch("venus_evcharger.control.idempotency.os.makedirs") as makedirs_mock,
        ):
            store = ControlApiIdempotencyStore(history_limit=2, path="/data/not-allowed.json")
            store.put("idem-1", "fp", 200, {"ok": True})
        open_mock.assert_not_called()
        makedirs_mock.assert_not_called()

        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/idempotency.json"
            first = ControlApiIdempotencyStore(history_limit=2, path=path)
            first.put("idem-1", "fp-1", 200, {"ok": True})
            first.put("idem-2", "fp-2", 202, {"ok": False})
            first.put("idem-3", "fp-3", 204, {"ok": "newest"})
            second = ControlApiIdempotencyStore(history_limit=2, path=path)

        self.assertEqual(second.count(), 2)
        self.assertEqual(second.path, path)
        self.assertIsNone(second.get("idem-1"))
        self.assertEqual(second.get("idem-2"), ("fp-2", 202, {"ok": False}))
        self.assertEqual(second.get("idem-3"), ("fp-3", 204, {"ok": "newest"}))

    def test_idempotency_store_default_path_and_history_contracts_are_exact(self) -> None:
        default_store = ControlApiIdempotencyStore()
        self.assertEqual(default_store.path, "")

        for index in range(201):
            default_store.put(f"idem-{index}", f"fp-{index}", 200 + index, {"index": index})

        self.assertEqual(default_store.count(), 200)
        self.assertIsNone(default_store.get("idem-0"))
        self.assertEqual(default_store.get("idem-1"), ("fp-1", 201, {"index": 1}))
        self.assertEqual(default_store.get("idem-200"), ("fp-200", 400, {"index": 200}))

        single_entry_store = ControlApiIdempotencyStore(history_limit=1)
        single_entry_store.put("old", "fp-old", 200, {"old": True})
        single_entry_store.put("new", "fp-new", 201, {"new": True})
        self.assertEqual(single_entry_store.count(), 1)
        self.assertIsNone(single_entry_store.get("old"))
        self.assertEqual(single_entry_store.get("new"), ("fp-new", 201, {"new": True}))

    def test_idempotency_store_copies_stored_and_returned_responses(self) -> None:
        store = ControlApiIdempotencyStore(history_limit=2)
        response = {"ok": True, "mutable": "original"}

        store.put("idem-1", "fp-1", "202", response)
        response["mutable"] = "changed-after-put"
        first = store.get("idem-1")
        assert first is not None
        first[2]["mutable"] = "changed-after-get"

        self.assertEqual(store.get("idem-1"), ("fp-1", 202, {"ok": True, "mutable": "original"}))

    def test_idempotency_store_runtime_persist_writes_exact_json_contract(self) -> None:
        file_handle = mock_open()
        with (
            patch("venus_evcharger.control.idempotency.os.path.exists", return_value=False),
            patch("venus_evcharger.control.idempotency.os.makedirs") as makedirs_mock,
            patch("venus_evcharger.control.idempotency.open", file_handle, create=True) as open_mock,
            patch("venus_evcharger.control.idempotency.json.dump") as dump_mock,
        ):
            store = ControlApiIdempotencyStore(history_limit=3, path="/run/control-idempotency.json")
            store.put("b-key", "fp-b", 202, {"ok": "b"})
            store.put("a-key", "fp-a", 200, {"ok": "a"})

        makedirs_mock.assert_any_call("/run", exist_ok=True)
        open_mock.assert_any_call("/run/control-idempotency.json", "w", encoding="utf-8")
        dump_mock.assert_called_with(
            {
                "b-key": {"fingerprint": "fp-b", "status": 202, "response": {"ok": "b"}},
                "a-key": {"fingerprint": "fp-a", "status": 200, "response": {"ok": "a"}},
            },
            file_handle(),
            sort_keys=True,
        )

    def test_idempotency_store_runtime_persist_keeps_sorted_json_on_disk(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/idempotency.json"
            store = ControlApiIdempotencyStore(history_limit=3, path=path)
            store.put("b-key", "fp-b", 202, {"ok": "b"})
            store.put("a-key", "fp-a", 200, {"ok": "a"})

            with open(path, "r", encoding="utf-8") as handle:
                persisted = handle.read()

        self.assertLess(persisted.index('"a-key"'), persisted.index('"b-key"'))
        self.assertIn('"fingerprint": "fp-a"', persisted)
        self.assertIn('"response": {"ok": "a"}', persisted)
        self.assertIn('"status": 200', persisted)

    def test_idempotency_store_ignores_invalid_runtime_payloads(self) -> None:
        with TemporaryDirectory() as tmpdir:
            invalid_json_path = f"{tmpdir}/invalid.json"
            with open(invalid_json_path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            invalid_json_store = ControlApiIdempotencyStore(history_limit=2, path=invalid_json_path)

            invalid_entries_path = f"{tmpdir}/entries.json"
            with open(invalid_entries_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "bad-non-dict": "x",
                        "bad-missing-fingerprint": {"status": 200, "response": {}},
                        "bad-empty-fingerprint": {"fingerprint": "", "status": 200, "response": {}},
                        "bad-whitespace-fingerprint": {"fingerprint": "  ", "status": 200, "response": {}},
                        "bad-response": {"fingerprint": "fp", "status": 200, "response": "x"},
                        "missing-status": {"fingerprint": " fp-missing-status ", "response": {"ok": "default-status"}},
                        "blank-status": {"fingerprint": "fp-blank-status", "status": "", "response": {"ok": "blank-status"}},
                        "good": {"fingerprint": "fp-good", "status": 201, "response": {"ok": True}},
                    },
                    handle,
                )
            invalid_entries_store = ControlApiIdempotencyStore(history_limit=5, path=invalid_entries_path)

        self.assertEqual(invalid_json_store.count(), 0)
        self.assertEqual(invalid_entries_store.count(), 3)
        self.assertEqual(invalid_entries_store.get("missing-status"), ("fp-missing-status", 0, {"ok": "default-status"}))
        self.assertEqual(invalid_entries_store.get("blank-status"), ("fp-blank-status", 0, {"ok": "blank-status"}))
        self.assertEqual(invalid_entries_store.get("good"), ("fp-good", 201, {"ok": True}))

    def test_idempotency_store_loader_uses_exact_open_and_logging_contracts(self) -> None:
        file_handle = mock_open(read_data="{}")
        with (
            patch("venus_evcharger.control.idempotency.open", file_handle, create=True) as open_mock,
            patch("venus_evcharger.control.idempotency.json.load", return_value={"idem": {"fingerprint": "fp", "response": {}}}) as load_mock,
        ):
            payload = ControlApiIdempotencyStore._loaded_payload("/run/control-idempotency.json")

        self.assertEqual(payload, {"idem": {"fingerprint": "fp", "response": {}}})
        open_mock.assert_called_once_with("/run/control-idempotency.json", "r", encoding="utf-8")
        load_mock.assert_called_once_with(file_handle())

        with patch("venus_evcharger.control.idempotency.logging.debug") as debug_log:
            self.assertIsNone(ControlApiIdempotencyStore._loaded_payload("/definitely/missing/idempotency.json"))

        debug_log.assert_called_once()
        self.assertEqual(debug_log.call_args.args[0], "Unable to load Control API idempotency store %s: %s")
        self.assertEqual(debug_log.call_args.args[1], "/definitely/missing/idempotency.json")
        self.assertIsInstance(debug_log.call_args.args[2], OSError)

    def test_idempotency_store_trims_loaded_runtime_entries_to_history_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/entries.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "idem-1": {"fingerprint": "fp-1", "status": 200, "response": {"ok": 1}},
                        "idem-2": {"fingerprint": "fp-2", "status": 201, "response": {"ok": 2}},
                        "idem-3": {"fingerprint": "fp-3", "status": 202, "response": {"ok": 3}},
                    },
                    handle,
                )
            store = ControlApiIdempotencyStore(history_limit=2, path=path)

        self.assertEqual(store.count(), 2)
        self.assertIsNone(store.get("idem-1"))
        self.assertEqual(store.get("idem-2"), ("fp-2", 201, {"ok": 2}))
        self.assertEqual(store.get("idem-3"), ("fp-3", 202, {"ok": 3}))

    def test_idempotency_store_logs_runtime_write_errors(self) -> None:
        with (
            patch("venus_evcharger.control.idempotency.open", side_effect=OSError("readonly"), create=True),
            patch("venus_evcharger.control.idempotency.logging.debug") as debug_log,
        ):
            ControlApiIdempotencyStore(history_limit=2, path="/run/control-idempotency.json").put(
                "idem-1",
                "fp",
                200,
                {"ok": True},
            )

        debug_log.assert_called_once()
        self.assertEqual(debug_log.call_args.args[0], "Unable to persist Control API idempotency store %s: %s")
        self.assertEqual(debug_log.call_args.args[1], "/run/control-idempotency.json")
        self.assertIsInstance(debug_log.call_args.args[2], OSError)
