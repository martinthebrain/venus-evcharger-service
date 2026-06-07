# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.backend.shelly_io import ShellyIoController
from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.runtime.support import RuntimeSupportController
from tests.venus_evcharger_stress_support import StressTestCaseBase, _FastStopEvent, _SteppingClock


class TestShellyWallboxStressRuntime(StressTestCaseBase):
    def test_runtime_support_worker_snapshot_survives_concurrent_access(self):
        service = SimpleNamespace(
            poll_interval_ms=1000,
            deviceinstance=60,
        )
        controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
        service._ensure_worker_state = controller.ensure_worker_state
        controller.ensure_worker_state()

        start = threading.Event()
        errors = []
        error_lock = threading.Lock()
        iterations = self._stress_iters()
        thread_count = self._stress_threads()

        def _record_error(message):
            with error_lock:
                errors.append(message)

        def writer(writer_id):
            start.wait()
            try:
                for sequence in range(iterations):
                    controller.update_worker_snapshot(
                        captured_at=float(sequence),
                        pm_status={"writer": writer_id, "sequence": sequence},
                        pv_power=float(writer_id * iterations + sequence),
                    )
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"writer-{writer_id}: {error}")

        def reader(reader_id):
            start.wait()
            try:
                for _ in range(iterations):
                    snapshot = controller.get_worker_snapshot()
                    pm_status = snapshot.get("pm_status")
                    if pm_status is None:
                        continue
                    if set(pm_status) != {"writer", "sequence"}:
                        _record_error(f"reader-{reader_id}: partial pm_status {pm_status}")
                    if not isinstance(pm_status["writer"], int) or not isinstance(pm_status["sequence"], int):
                        _record_error(f"reader-{reader_id}: invalid pm_status {pm_status}")
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"reader-{reader_id}: {error}")

        threads = []
        for writer_id in range(thread_count):
            threads.append(threading.Thread(target=writer, args=(writer_id,), daemon=True))
        for reader_id in range(thread_count):
            threads.append(threading.Thread(target=reader, args=(reader_id,), daemon=True))

        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        final_snapshot = controller.get_worker_snapshot()
        self.assertIn("captured_at", final_snapshot)
        self.assertIn("pm_status", final_snapshot)

    def test_relay_queue_survives_concurrent_writers_and_consumers(self):
        service = SimpleNamespace(
            poll_interval_ms=1000,
            deviceinstance=60,
            _time_now=_SteppingClock(),
        )
        runtime = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
        service._ensure_worker_state = runtime.ensure_worker_state
        runtime.ensure_worker_state()
        controller = ShellyIoController(service)

        start = threading.Event()
        errors = []
        error_lock = threading.Lock()
        iterations = self._stress_iters()
        thread_count = self._stress_threads()

        def _record_error(message):
            with error_lock:
                errors.append(message)

        def writer(offset):
            start.wait()
            try:
                for index in range(iterations):
                    controller.queue_relay_command(bool((index + offset) % 2))
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"writer-{offset}: {error}")

        def consumer(consumer_id):
            start.wait()
            try:
                for _ in range(iterations):
                    pending_state, pending_at = controller.peek_pending_relay_command()
                    if (pending_state is None) != (pending_at is None):
                        _record_error(
                            f"consumer-{consumer_id}: torn queue state "
                            f"pending_state={pending_state} pending_at={pending_at}"
                        )
                    if pending_state is not None:
                        controller.clear_pending_relay_command(pending_state)
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"consumer-{consumer_id}: {error}")

        threads = []
        for writer_id in range(thread_count):
            threads.append(threading.Thread(target=writer, args=(writer_id,), daemon=True))
        for consumer_id in range(thread_count):
            threads.append(threading.Thread(target=consumer, args=(consumer_id,), daemon=True))

        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        pending_state, pending_at = controller.peek_pending_relay_command()
        self.assertEqual(pending_state is None, pending_at is None)

    def test_io_worker_loop_survives_prolonged_fault_injection(self):
        iterations = self._stress_iters()
        stop_event = _FastStopEvent()
        read_counter = {"count": 0}

        def fetch_pm_status():
            read_counter["count"] += 1
            current = read_counter["count"]
            if current >= iterations:
                stop_event.set()
            if current % 5 == 0:
                raise RuntimeError("fault-injected Shelly read failure")
            return {"output": bool(current % 2), "apower": float(current)}

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_stop_event=stop_event,
            _worker_poll_interval_seconds=0.001,
            _time_now=_SteppingClock(),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=fetch_pm_status,
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=0.001,
        )
        controller = ShellyIoController(service)

        controller.io_worker_loop()

        self.assertGreaterEqual(read_counter["count"], iterations)
        self.assertGreater(service._mark_failure.call_count, 0)
        self.assertGreater(service._mark_recovery.call_count, 0)
        self.assertGreater(service._update_worker_snapshot.call_count, 0)
        self.assertGreater(service._warning_throttled.call_count, 0)

    def test_auto_input_supervisor_handles_fault_injected_snapshot_stream(self):
        iterations = self._stress_iters()
        updates = []
        errors = []
        updates_lock = threading.Lock()
        errors_lock = threading.Lock()
        start = threading.Event()

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = os.path.join(temp_dir, "auto-input.json")

            def load_json_file(path):
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)

            def update_worker_snapshot(**fields):
                with updates_lock:
                    updates.append(dict(fields))

            service = SimpleNamespace(
                _ensure_worker_state=MagicMock(),
                auto_input_snapshot_path=snapshot_path,
                auto_input_helper_stale_seconds=5.0,
                auto_input_helper_restart_seconds=1.0,
                _time_now=lambda: 0.0,
                _stat_path=os.stat,
                _load_json_file=load_json_file,
                _warning_throttled=MagicMock(),
                _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
                virtual_mode=1,
                _auto_input_runtime_instance_id="instance-1",
                _auto_input_snapshot_mtime_ns=None,
                _auto_input_snapshot_last_seen=None,
                _update_worker_snapshot=update_worker_snapshot,
            )
            controller = AutoInputSupervisor(service)

            def _record_error(message):
                with errors_lock:
                    errors.append(message)

            def writer():
                start.wait()
                try:
                    for index in range(iterations):
                        if index % 4 == 0:
                            payload = "{broken-json"
                        elif index % 4 == 1:
                            payload = json.dumps(["not-a-dict"])
                        elif index % 4 == 2:
                            payload = json.dumps(
                                {
                                    "snapshot_version": 1,
                                    "captured_at": float(index),
                                    "heartbeat_at": float(index),
                                    "pv_captured_at": float(index),
                                    "pv_power": 1200.0,
                                    "battery_captured_at": float(index),
                                    "battery_soc": 55.0,
                                    "grid_captured_at": float(index),
                                    "grid_power": -800.0,
                                    "writer_pid": 4321,
                                    "helper_generation": 1,
                                    "runtime_instance_id": "instance-1",
                                }
                            )
                        else:
                            payload = json.dumps(
                                {
                                    "snapshot_version": 1,
                                    "captured_at": float(index - 100),
                                    "heartbeat_at": float(index - 100),
                                    "pv_captured_at": float(index - 100),
                                    "pv_power": 900.0,
                                    "battery_captured_at": float(index - 100),
                                    "battery_soc": 50.0,
                                    "grid_captured_at": float(index - 100),
                                    "grid_power": -500.0,
                                    "writer_pid": 4321,
                                    "helper_generation": 1,
                                    "runtime_instance_id": "instance-1",
                                }
                            )
                        write_text_atomically(snapshot_path, payload)
                except Exception as error:  # pylint: disable=broad-except
                    _record_error(f"writer: {error}")

            def reader():
                start.wait()
                try:
                    for index in range(iterations):
                        controller.refresh_snapshot(now=float(index))
                except Exception as error:  # pylint: disable=broad-except
                    _record_error(f"reader: {error}")

            writer_thread = threading.Thread(target=writer, daemon=True)
            reader_thread = threading.Thread(target=reader, daemon=True)
            writer_thread.start()
            reader_thread.start()
            start.set()
            writer_thread.join()
            reader_thread.join()

            write_text_atomically(snapshot_path, "{broken-json")
            controller.refresh_snapshot(now=float(iterations + 0.25))

            write_text_atomically(snapshot_path, json.dumps(["not-a-dict"]))
            controller.refresh_snapshot(now=float(iterations + 0.5))

            write_text_atomically(
                snapshot_path,
                json.dumps(
                    {
                        "snapshot_version": 1,
                        "captured_at": float(iterations - 100),
                        "heartbeat_at": float(iterations - 100),
                        "pv_captured_at": float(iterations - 100),
                        "pv_power": 900.0,
                        "battery_captured_at": float(iterations - 100),
                        "battery_soc": 50.0,
                        "grid_captured_at": float(iterations - 100),
                        "grid_power": -500.0,
                        "writer_pid": 4321,
                        "helper_generation": 1,
                        "runtime_instance_id": "instance-1",
                    }
                ),
            )
            controller.refresh_snapshot(now=float(iterations + 10))

            write_text_atomically(
                snapshot_path,
                json.dumps(
                    {
                        "snapshot_version": 1,
                        "captured_at": float(iterations + 1),
                        "heartbeat_at": float(iterations + 1),
                        "pv_captured_at": float(iterations + 1),
                        "pv_power": 1200.0,
                        "battery_captured_at": float(iterations + 1),
                        "battery_soc": 55.0,
                        "grid_captured_at": float(iterations + 1),
                        "grid_power": -800.0,
                        "writer_pid": 4321,
                        "helper_generation": 1,
                        "runtime_instance_id": "instance-1",
                    }
                ),
            )
            controller.refresh_snapshot(now=float(iterations + 1))

        self.assertEqual(errors, [])
        self.assertGreater(len(updates), 0)
        self.assertGreater(service._warning_throttled.call_count, 0)
        self.assertTrue(any(update.get("pv_power") is None for update in updates))
        self.assertTrue(any(update.get("pv_power") == 1200.0 for update in updates))
