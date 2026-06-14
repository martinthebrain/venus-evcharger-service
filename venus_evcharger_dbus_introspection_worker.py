#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Slow advisory DBus introspection worker for the Venus EV charger service.

The worker is intentionally separate from the Auto input helper. It performs
explicit, timeout-controlled DBus Introspect calls in a throttled queue and
writes a volatile JSON mapping. Charger runtime code may use the mapping as a
freshness/confidence hint, but must never wait for it.
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import signal
import time
import xml.etree.ElementTree as xml_et
from dataclasses import dataclass, field
from typing import Any

from venus_evcharger.core.shared import compact_json, parse_config_bool, prefixed_service_names, write_text_atomically
from venus_evcharger.dbus_introspection import DBUS_INTROSPECTION_SCHEMA_VERSION
from venus_evcharger.dbus_gateway import DbusCacheStore, GatewayClient, gateway_paths

# Compatibility patch target for older tests. The worker never dereferences this.
dbus: Any = None


@dataclass(order=True)
class IntrospectionJob:
    sort_key: tuple[int, float, float] = field(init=False, repr=False)
    service: str
    path: str
    priority: int = 0
    source: str = "background"
    reason: str = ""
    due_at: float = 0.0
    requested_at: float = 0.0

    def __post_init__(self) -> None:
        self.sort_key = (-int(self.priority), float(self.due_at), float(self.requested_at))

    @property
    def key(self) -> tuple[str, str]:
        return self.service, self.path


class DbusIntrospectionWorker:
    """Throttle explicit DBus Introspect calls and publish advisory findings."""

    def __init__(
        self,
        config_path: str,
        snapshot_path: str | None = None,
        request_path: str | None = None,
        parent_pid: object = None,
    ) -> None:
        self.config_path = config_path
        self.config = self._load_config(config_path)
        deviceinstance = int(float(self.config.get("DeviceInstance", 60) or 60))
        self.snapshot_path = snapshot_path or self.config.get(
            "DbusIntrospectionSnapshotPath",
            f"/run/dbus-venus-evcharger-dbus-map-{deviceinstance}.json",
        ).strip()
        self.request_path = request_path or self.config.get(
            "DbusIntrospectionRequestPath",
            f"/run/dbus-venus-evcharger-dbus-map-requests-{deviceinstance}.json",
        ).strip()
        self.parent_pid = int(parent_pid) if isinstance(parent_pid, (str, int)) else None
        self.enabled = self._as_bool(self.config.get("DbusIntrospectionEnabled", "1"), True)
        self.tick_seconds = max(0.5, float(self.config.get("DbusIntrospectionTickSeconds", 5.0)))
        self.full_scan_interval_seconds = max(60.0, float(self.config.get("DbusIntrospectionFullScanIntervalSeconds", 21600.0)))
        self.min_job_interval_seconds = max(0.1, float(self.config.get("DbusIntrospectionMinJobIntervalSeconds", 2.0)))
        self.retry_base_seconds = max(5.0, float(self.config.get("DbusIntrospectionRetryBaseSeconds", 900.0)))
        self.retry_max_seconds = max(self.retry_base_seconds, float(self.config.get("DbusIntrospectionRetryMaxSeconds", 10800.0)))
        self.timeout_seconds = max(0.2, float(self.config.get("DbusIntrospectionTimeoutSeconds", self.config.get("DbusMethodTimeoutSeconds", 1.0))))
        self.max_services = max(1, int(float(self.config.get("DbusIntrospectionMaxServicesPerPrefix", 10) or 10)))
        self.load_avg_max = max(0.0, float(self.config.get("DbusIntrospectionLoadAvgMax", 3.0)))
        self.min_mem_available_kb = max(0, int(float(self.config.get("DbusIntrospectionMinMemAvailableKb", 32768) or 32768)))
        self.pv_quiet_hours = self.config.get("DbusIntrospectionPvQuietHours", "22:00-05:00").strip()
        self._stop_requested = False
        self._system_bus = None
        self._last_full_scan_at = 0.0
        self._last_job_at = 0.0
        self._jobs: dict[tuple[str, str], IntrospectionJob] = {}
        self._failures: dict[tuple[str, str], int] = {}
        self._services: dict[str, dict[str, Any]] = {}
        self._last_service_names: list[str] = []
        run_dir = self.config.get("DbusGatewayRunDir", "/run/venus-evcharger").strip()
        self.gateway_paths = gateway_paths(run_dir or None)
        self.gateway_client = GatewayClient(self.gateway_paths)

    @staticmethod
    def _load_config(config_path: str) -> configparser.SectionProxy:
        parser = configparser.ConfigParser()
        loaded = parser.read(config_path)
        if not loaded or "DEFAULT" not in parser:
            raise ValueError(f"Unable to read config file: {config_path}")
        return parser["DEFAULT"]

    _as_bool = staticmethod(parse_config_bool)

    def stop(self, *_args: object) -> None:
        self._stop_requested = True

    def run_forever(self) -> int:
        if not self.enabled:
            self._write_snapshot("disabled")
            return 0
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self._write_snapshot("starting")
        while not self._stop_requested and self._parent_alive():
            self.run_once()
            time.sleep(self.tick_seconds)
        self._write_snapshot("stopped")
        return 0

    def run_once(self, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        self._enqueue_request_file_jobs(current)
        if current - self._last_full_scan_at >= self.full_scan_interval_seconds or not self._last_service_names:
            self._enqueue_background_jobs(current)
        if self._resource_gate_active():
            return self._write_snapshot("deferred-resource-gate")
        if current - self._last_job_at >= self.min_job_interval_seconds:
            job = self._next_due_job(current)
            if job is not None:
                self._process_job(job, current)
                self._last_job_at = current
        return self._write_snapshot("running")

    def _parent_alive(self) -> bool:
        if self.parent_pid is None:
            return True
        try:
            os.kill(self.parent_pid, 0)
        except ProcessLookupError:
            return False
        except Exception:
            return True
        return True

    def _get_system_bus(self) -> Any:
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def _reset_system_bus(self) -> None:
        self._system_bus = None

    def _list_dbus_services(self) -> list[str]:
        snapshot = DbusCacheStore.load_snapshot(self.gateway_paths.cache_path)
        services = snapshot.get("services", [])
        if isinstance(services, list) and services:
            return [str(name) for name in services]
        if isinstance(services, dict) and services:
            return [str(name) for name in services]
        self.gateway_client.enqueue_command({"kind": "refresh_services", "source": "introspection-worker", "priority": "discovery"})
        return []

    def _enqueue_background_jobs(self, now: float) -> None:
        try:
            names = self._list_dbus_services()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("DBus introspection worker could not list services: %s", error)
            self._reset_system_bus()
            return
        self._last_service_names = names
        self._last_full_scan_at = now
        self._prune_missing_services(names)
        for service, path, priority, source, reason in self._background_specs(names):
            if source == "pv" and self._pv_quiet_now(now):
                self._enqueue_job(service, path, priority=1, source=source, reason="pv-quiet-hours", due_at=now + self.retry_base_seconds)
                continue
            self._enqueue_job(service, path, priority=priority, source=source, reason=reason, due_at=now)

    def _prune_missing_services(self, names: list[str]) -> None:
        current_names = set(names)
        for service in list(self._services):
            if service not in current_names:
                self._services.pop(service, None)
        for key in list(self._failures):
            if key[0] not in current_names:
                self._failures.pop(key, None)
        for key, job in list(self._jobs.items()):
            if key[0] not in current_names and job.source != "request":
                self._jobs.pop(key, None)

    def _background_specs(self, names: list[str]) -> list[tuple[str, str, int, str, str]]:
        specs: list[tuple[str, str, int, str, str]] = []
        grid_service = self.config.get("AutoGridService", "com.victronenergy.system").strip()
        for path in (
            self.config.get("AutoGridL1Path", "/Ac/Grid/L1/Power").strip(),
            self.config.get("AutoGridL2Path", "/Ac/Grid/L2/Power").strip(),
            self.config.get("AutoGridL3Path", "/Ac/Grid/L3/Power").strip(),
        ):
            if grid_service and path:
                specs.append((grid_service, path, 80, "grid", "configured-grid-path"))
        battery_service = self.config.get("AutoBatteryService", "").strip()
        battery_prefix = self.config.get("AutoBatteryServicePrefix", "com.victronenergy.battery").strip()
        battery_path = self.config.get("AutoBatterySocPath", "/Soc").strip()
        for service in self._explicit_or_prefixed_services(battery_service, battery_prefix, names):
            if battery_path:
                specs.append((service, battery_path, 70, "battery", "battery-service-discovery"))
        pv_service = self.config.get("AutoPvService", "").strip()
        pv_prefix = self.config.get("AutoPvServicePrefix", "com.victronenergy.pvinverter").strip()
        pv_path = self.config.get("AutoPvPath", "/Ac/Power").strip()
        for service in self._explicit_or_prefixed_services(pv_service, pv_prefix, names):
            if pv_path:
                specs.append((service, pv_path, 30, "pv", "pv-service-discovery"))
        if self._as_bool(self.config.get("AutoUseDcPv", "1"), True):
            dc_service = self.config.get("AutoDcPvService", "com.victronenergy.system").strip()
            dc_path = self.config.get("AutoDcPvPath", "/Dc/Pv/Power").strip()
            if dc_service and dc_path:
                specs.append((dc_service, dc_path, 60, "pv", "dc-pv-configured-path"))
        return specs

    def _explicit_or_prefixed_services(self, explicit_service: str, prefix: str, names: list[str]) -> list[str]:
        if explicit_service:
            return [explicit_service] if explicit_service in names else []
        return prefixed_service_names(names, prefix, self.max_services, sort_names=False)

    def _enqueue_request_file_jobs(self, now: float) -> None:
        payload = self._read_request_payload()
        requests = payload.get("requests", [])
        if not isinstance(requests, list):
            return
        accepted: list[dict[str, Any]] = []
        for request in requests:
            if not isinstance(request, dict):
                continue
            service = str(request.get("service", "") or "").strip()
            path = str(request.get("path", "") or "").strip()
            if not service or not path:
                continue
            accepted.append(request)
            self._enqueue_job(
                service,
                path,
                priority=int(request.get("priority", 100) or 100),
                source=str(request.get("source", "request") or "request"),
                reason=str(request.get("reason", "requested") or "requested"),
                due_at=now,
            )
        if accepted:
            self._clear_request_payload()

    def _read_request_payload(self) -> dict[str, Any]:
        try:
            with open(self.request_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _clear_request_payload(self) -> None:
        try:
            write_text_atomically(self.request_path, compact_json({"requests": []}))
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to clear DBus introspection request payload %s: %s", self.request_path, error)

    def _enqueue_job(
        self,
        service: str,
        path: str,
        *,
        priority: int,
        source: str,
        reason: str,
        due_at: float,
    ) -> None:
        key = (service, path)
        existing = self._jobs.get(key)
        if existing is not None:
            existing_due = float(existing.due_at)
            new_due = float(due_at)
            if existing_due < new_due or (existing_due <= new_due and existing.priority >= priority):
                return
        self._jobs[key] = IntrospectionJob(service, path, priority, source, reason, due_at, time.time())

    def _next_due_job(self, now: float) -> IntrospectionJob | None:
        due = [job for job in self._jobs.values() if job.due_at <= now]
        if not due:
            return None
        due.sort(key=lambda job: job.sort_key)
        job = due[0]
        self._jobs.pop(job.key, None)
        return job

    def _process_job(self, job: IntrospectionJob, now: float) -> None:
        try:
            finding = self._introspect(job, now)
        except Exception as error:  # pylint: disable=broad-except
            finding = self._error_finding(job, error, now)
            self._reset_system_bus()
        self._store_finding(job.service, job.path, finding)

    def _introspect(self, job: IntrospectionJob, now: float) -> dict[str, Any]:
        self.gateway_client.enqueue_command(
            {
                "kind": "introspect",
                "service": job.service,
                "path": job.path,
                "priority": "discovery",
                "source": job.source,
                "reason": job.reason,
                "timeout": self.timeout_seconds,
                "coalesce_key": f"introspect:{job.service}:{job.path}",
            }
        )
        interfaces: list[str] = []
        children: list[str] = []
        self._failures.pop(job.key, None)
        return {
            "status": "requested",
            "confidence": 0.5,
            "interfaces": interfaces,
            "children": children,
            "source": job.source,
            "reason": job.reason,
            "last_success_at": None,
            "last_error": "",
            "retry_after": now + self.min_job_interval_seconds,
        }

    @staticmethod
    def _parse_introspection_xml(xml_data: object) -> tuple[list[str], list[str]]:
        root = xml_et.fromstring(str(xml_data))
        interfaces = [str(node.attrib.get("name", "")) for node in root.findall("interface") if node.attrib.get("name")]
        children = [str(node.attrib.get("name", "")) for node in root.findall("node") if node.attrib.get("name")]
        return interfaces, children

    def _error_finding(self, job: IntrospectionJob, error: Exception, now: float) -> dict[str, Any]:
        failures = self._failures.get(job.key, 0) + 1
        self._failures[job.key] = failures
        status = "known-missing" if self._is_missing_dbus_error(error) else "unresponsive-backoff"
        retry_delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** max(0, failures - 1)))
        return {
            "status": status,
            "confidence": 0.9 if status == "known-missing" else 0.55,
            "interfaces": [],
            "children": [],
            "source": job.source,
            "reason": job.reason,
            "last_success_at": None,
            "last_error": str(error),
            "failure_count": failures,
            "retry_after": now + retry_delay,
        }

    @staticmethod
    def _is_missing_dbus_error(error: Exception) -> bool:
        getter = getattr(error, "get_dbus_name", None)
        name = ""
        if callable(getter):
            try:
                name = str(getter() or "")
            except Exception:
                name = ""
        text = str(error)
        return any(marker in name or marker in text for marker in ("Unknown", "NameHasNoOwner", "ServiceUnknown"))

    def _store_finding(self, service: str, path: str, finding: dict[str, Any]) -> None:
        service_payload = self._services.setdefault(service, {"paths": {}})
        paths = service_payload.setdefault("paths", {})
        if isinstance(paths, dict):
            paths[path] = finding
        service_payload["last_updated_at"] = time.time()

    def _resource_gate_active(self) -> bool:
        return self._load_gate_active() or self._memory_gate_active()

    def _load_gate_active(self) -> bool:
        if self.load_avg_max <= 0.0:
            return False
        try:
            return os.getloadavg()[0] > self.load_avg_max
        except Exception:
            return False

    def _memory_gate_active(self) -> bool:
        if self.min_mem_available_kb <= 0:
            return False
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) < self.min_mem_available_kb
        except Exception:
            return False
        return False

    def _pv_quiet_now(self, now: float) -> bool:
        window = self.pv_quiet_hours
        if not window or "-" not in window:
            return False
        start_text, end_text = window.split("-", 1)
        try:
            start = self._hhmm_minutes(start_text)
            end = self._hhmm_minutes(end_text)
        except ValueError:
            return False
        current = time.gmtime(now)
        minute = current.tm_hour * 60 + current.tm_min
        if start <= end:
            return start <= minute < end
        return minute >= start or minute < end

    @staticmethod
    def _hhmm_minutes(text: str) -> int:
        hour_text, minute_text = text.strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(text)
        return hour * 60 + minute

    def _write_snapshot(self, state: str) -> dict[str, Any]:
        now = time.time()
        payload = {
            "schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION,
            "captured_at": now,
            "heartbeat_at": now,
            "worker_state": state,
            "writer_pid": os.getpid(),
            "queue_depth": len(self._jobs),
            "last_full_scan_at": self._last_full_scan_at,
            "services": self._services,
        }
        try:
            write_text_atomically(self.snapshot_path, compact_json(payload))
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to write DBus introspection snapshot %s: %s", self.snapshot_path, error)
        return payload


def main(argv: list[str] | None = None) -> int:
    script_dir = os.path.dirname(os.path.realpath(__file__))
    default_config_path = os.path.join(script_dir, "deploy/venus/config.venus_evcharger.ini")
    parser = argparse.ArgumentParser(description="Run the advisory Venus EV charger DBus introspection worker.")
    parser.add_argument("config_path", nargs="?", default=default_config_path)
    parser.add_argument("snapshot_path", nargs="?")
    parser.add_argument("request_path", nargs="?")
    parser.add_argument("parent_pid", nargs="?")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
    return DbusIntrospectionWorker(args.config_path, args.snapshot_path, args.request_path, args.parent_pid).run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
