# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenario contracts for runtime-state persistence composition."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.controllers.state_persistence import RuntimeStatePersistence
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer


def _restore_calls() -> list[tuple[dict[str, object], float]]:
    return []


@dataclass
class SnapshotFixture:
    payload: dict[str, object]
    calls: int = 0

    def build(self) -> dict[str, object]:
        self.calls += 1
        return dict(self.payload)


@dataclass
class RestoreRecorder:
    calls: list[tuple[dict[str, object], float]] = field(default_factory=_restore_calls)

    def restore(self, state: dict[str, object], current_time: float) -> None:
        self.calls.append((dict(state), current_time))


@dataclass
class SummaryFixture:
    value: str = "mode=2"
    calls: int = 0

    def build(self) -> str:
        self.calls += 1
        return self.value


def _persistence(
    service: object,
    snapshot: SnapshotFixture,
    restorer: RestoreRecorder,
    summary: SummaryFixture,
) -> RuntimeStatePersistence:
    return RuntimeStatePersistence(
        service,
        RuntimeStateNormalizer(),
        snapshot,
        restorer,
        summary,
    )


class TestRuntimeStatePersistenceContracts(unittest.TestCase):
    def test_load_restores_payload_and_records_normalized_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"mode":2,"state_changed_at":1200.0}', encoding="utf-8")
            service = SimpleNamespace(runtime_state_path=f" {path} ", time_now=lambda: 1234.0)
            snapshot = SnapshotFixture({"mode": 2})
            restorer = RestoreRecorder()
            summary = SummaryFixture()
            persistence = _persistence(service, snapshot, restorer, summary)

            with (
                patch("venus_evcharger.controllers.state_persistence.logging.info") as info,
                patch("venus_evcharger.controllers.state_persistence.write_text_atomically") as write_state,
            ):
                persistence.load()

        self.assertEqual(restorer.calls, [({"mode": 2}, 1234.0)])
        self.assertEqual(service._runtime_state_serialized, '{"mode":2}')
        self.assertEqual(service._runtime_state_changed_at, 1200.0)
        self.assertEqual(snapshot.calls, 1)
        self.assertEqual(summary.calls, 1)
        write_state.assert_not_called()
        info.assert_called_once_with("Restored runtime state from %s: %s", str(path), "mode=2")

    def test_load_and_save_ignore_absent_paths_and_invalid_payloads(self) -> None:
        snapshot = SnapshotFixture({"mode": 2})
        restorer = RestoreRecorder()
        summary = SummaryFixture()
        persistence = _persistence(SimpleNamespace(), snapshot, restorer, summary)
        with patch("venus_evcharger.controllers.state_persistence.read_json_object_file") as read_state:
            persistence.load()
            persistence.save()
        read_state.assert_not_called()
        self.assertEqual(snapshot.calls, 0)
        self.assertEqual(restorer.calls, [])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("[]", encoding="utf-8")
            _persistence(SimpleNamespace(runtime_state_path=str(path)), snapshot, restorer, summary).load()
        self.assertEqual(restorer.calls, [])

    def test_load_migrates_legacy_state_once_using_event_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"mode":2}', encoding="utf-8")
            service = SimpleNamespace(runtime_state_path=str(path), time_now=lambda: 1234.0)
            persistence = _persistence(
                service,
                SnapshotFixture({"mode": 2}),
                RestoreRecorder(),
                SummaryFixture(),
            )

            with patch(
                "venus_evcharger.controllers.state_persistence.os.path.getmtime",
                return_value=1200.0,
            ) as getmtime:
                persistence.load()

            getmtime.assert_called_once_with(str(path))
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '{"mode":2,"state_changed_at":1200.0}',
            )
            persistence.load()

        self.assertEqual(service._runtime_state_serialized, '{"mode":2}')
        self.assertEqual(service._runtime_state_changed_at, 1200.0)

    def test_load_normalizes_invalid_metadata_without_repeated_migration_attempts(self) -> None:
        invalid_values: tuple[object, ...] = (
            True,
            "123",
            float("nan"),
            -1.0,
            2000.0,
        )
        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                service = SimpleNamespace(runtime_state_path="/run/state.json", time_now=lambda: 1000.0)
                persistence = _persistence(
                    service,
                    SnapshotFixture({"mode": 2}),
                    RestoreRecorder(),
                    SummaryFixture(),
                )
                with (
                    patch(
                        "venus_evcharger.controllers.state_persistence.read_json_object_file",
                        return_value={"mode": 2, "state_changed_at": invalid_value},
                    ),
                    patch(
                        "venus_evcharger.controllers.state_persistence.os.path.getmtime",
                        side_effect=OSError("missing"),
                    ),
                    patch(
                        "venus_evcharger.controllers.state_persistence.write_text_atomically",
                        side_effect=OSError("read-only"),
                    ),
                    patch("venus_evcharger.controllers.state_persistence.logging.warning") as warning,
                ):
                    persistence.load()
                    persistence.save()

                self.assertEqual(service._runtime_state_serialized, '{"mode":2}')
                self.assertEqual(service._runtime_state_changed_at, 1000.0)
                warning.assert_called_once()

    def test_load_clamps_subsecond_future_metadata_to_current_time(self) -> None:
        service = SimpleNamespace(runtime_state_path="/run/state.json", time_now=lambda: 1000.0)
        persistence = _persistence(
            service,
            SnapshotFixture({"mode": 2}),
            RestoreRecorder(),
            SummaryFixture(),
        )
        with (
            patch(
                "venus_evcharger.controllers.state_persistence.read_json_object_file",
                return_value={"mode": 2, "state_changed_at": 1000.5},
            ),
            patch("venus_evcharger.controllers.state_persistence.write_text_atomically") as write_state,
        ):
            persistence.load()

        self.assertEqual(service._runtime_state_changed_at, 1000.0)
        write_state.assert_not_called()

    def test_load_accepts_exact_epoch_and_future_tolerance_boundaries(self) -> None:
        for changed_at, current_time, expected in (
            (0.0, 0.0, 0.0),
            (0.5, 0.5, 0.5),
            (1001.0, 1000.0, 1000.0),
        ):
            with self.subTest(changed_at=changed_at, current_time=current_time):
                service = SimpleNamespace(
                    runtime_state_path="/run/state.json",
                    time_now=lambda value=current_time: value,
                )
                restorer = RestoreRecorder()
                persistence = _persistence(
                    service,
                    SnapshotFixture({"mode": 2}),
                    restorer,
                    SummaryFixture(),
                )
                with (
                    patch(
                        "venus_evcharger.controllers.state_persistence.read_json_object_file",
                        return_value={
                            "mode": 2,
                            "state_changed_at": changed_at,
                        },
                    ),
                    patch(
                        "venus_evcharger.controllers.state_persistence.write_text_atomically"
                    ) as write_state,
                ):
                    persistence.load()

                self.assertEqual(restorer.calls, [({"mode": 2}, current_time)])
                self.assertEqual(service._runtime_state_changed_at, expected)
                write_state.assert_not_called()

    def test_load_rejects_timestamp_beyond_future_tolerance(self) -> None:
        service = SimpleNamespace(
            runtime_state_path="/run/state.json",
            time_now=lambda: 1000.0,
        )
        persistence = _persistence(
            service,
            SnapshotFixture({"mode": 2}),
            RestoreRecorder(),
            SummaryFixture(),
        )
        with (
            patch(
                "venus_evcharger.controllers.state_persistence.read_json_object_file",
                return_value={"mode": 2, "state_changed_at": 1001.5},
            ),
            patch(
                "venus_evcharger.controllers.state_persistence.os.path.getmtime",
                side_effect=OSError("missing"),
            ),
            patch(
                "venus_evcharger.controllers.state_persistence.write_text_atomically"
            ) as write_state,
        ):
            persistence.load()

        self.assertEqual(service._runtime_state_changed_at, 1000.0)
        write_state.assert_called_once()

    def test_save_writes_changed_payload_once_and_skips_unchanged_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            service = SimpleNamespace(runtime_state_path=f" {path} ")
            snapshot = SnapshotFixture({"mode": 2})
            summary = SummaryFixture()
            persistence = _persistence(service, snapshot, RestoreRecorder(), summary)

            with (
                patch("venus_evcharger.controllers.state_persistence.time.time", return_value=2000.0),
                patch("venus_evcharger.controllers.state_persistence.logging.debug") as debug,
            ):
                persistence.save()
                persistence.save()

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '{"mode":2,"state_changed_at":2000.0}',
            )
        self.assertEqual(service._runtime_state_serialized, '{"mode":2}')
        self.assertEqual(service._runtime_state_changed_at, 2000.0)
        self.assertEqual(snapshot.calls, 2)
        self.assertEqual(summary.calls, 1)
        debug.assert_called_once_with("Saved runtime state to %s: %s", str(path), "mode=2")

    def test_save_updates_event_timestamp_only_when_semantic_state_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            service = SimpleNamespace(runtime_state_path=str(path))
            snapshot = SnapshotFixture({"mode": 2})
            persistence = _persistence(service, snapshot, RestoreRecorder(), SummaryFixture())

            with patch(
                "venus_evcharger.controllers.state_persistence.time.time",
                side_effect=(100.0, 200.0),
            ):
                persistence.save()
                persistence.save()
                snapshot.payload["mode"] = 1
                persistence.save()

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '{"mode":1,"state_changed_at":200.0}',
            )
        self.assertEqual(service._runtime_state_changed_at, 200.0)

    def test_save_preserves_zero_epoch_boundary(self) -> None:
        service = SimpleNamespace(runtime_state_path="/run/state.json")
        persistence = _persistence(
            service,
            SnapshotFixture({"mode": 2}),
            RestoreRecorder(),
            SummaryFixture(),
        )
        with (
            patch(
                "venus_evcharger.controllers.state_persistence.time.time",
                return_value=0.0,
            ),
            patch(
                "venus_evcharger.controllers.state_persistence.write_text_atomically"
            ) as write_state,
        ):
            persistence.save()

        write_state.assert_called_once_with(
            "/run/state.json",
            '{"mode":2,"state_changed_at":0.0}',
        )
        self.assertEqual(service._runtime_state_changed_at, 0.0)

    def test_save_failure_keeps_previous_cache_and_reports_boundary_error(self) -> None:
        service = SimpleNamespace(
            runtime_state_path="/run/evcharger-state.json",
            _runtime_state_serialized="old-payload",
        )
        persistence = _persistence(
            service,
            SnapshotFixture({"mode": 2}),
            RestoreRecorder(),
            SummaryFixture(),
        )
        with (
            patch(
                "venus_evcharger.controllers.state_persistence.write_text_atomically",
                side_effect=OSError("read-only"),
            ),
            patch("venus_evcharger.controllers.state_persistence.logging.warning") as warning,
        ):
            persistence.save()

        self.assertEqual(service._runtime_state_serialized, "old-payload")
        self.assertFalse(hasattr(service, "_runtime_state_changed_at"))
        message, path, error = warning.call_args.args
        self.assertEqual(message, "Unable to write runtime state to %s: %s")
        self.assertEqual(path, "/run/evcharger-state.json")
        self.assertEqual(str(error), "read-only")
