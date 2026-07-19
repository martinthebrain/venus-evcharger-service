#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify a deployment receipt with little work on resource-constrained GX devices."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MANAGED_ROOTS = (
    "install.sh",
    "LICENSE",
    "README.md",
    "SHELLY_PROFILES.md",
    "version.txt",
    "venus_evcharger_service.py",
    "venus_evcharger_dbus_adapter.py",
    "venus_evcharger_observer.py",
    "venus_evcharger_auto_input_helper.py",
    "venus_evchargerctl.py",
    "deploy/venus",
    "venus_evcharger",
    "scripts/ops",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _normalized_hashes(value: object, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(_validated_hash_item(item, label=label) for item in value.items())


def _validated_relative_path(raw_path: object, *, label: str) -> str:
    relative_path = str(raw_path)
    parsed_path = Path(relative_path)
    if parsed_path.is_absolute() or ".." in parsed_path.parts:
        raise ValueError(f"Unsafe relative path in {label}: {relative_path}")
    return relative_path


def _validated_hash_item(item: tuple[object, object], *, label: str) -> tuple[str, str]:
    relative_path = _validated_relative_path(item[0], label=label)
    expected_hash = str(item[1])
    if not SHA256_PATTERN.fullmatch(expected_hash):
        raise ValueError(f"Invalid SHA-256 in {label}: {relative_path}")
    return relative_path, expected_hash


def _normalized_missing_paths(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("missing_critical_files must be a JSON array")
    return [_validated_relative_path(item, label="missing_critical_files") for item in value]


def _active_root(target_dir: Path, receipt: Mapping[str, Any]) -> Path:
    current_root = target_dir / "current"
    active_root = (current_root if current_root.is_dir() else target_dir).resolve()
    configured_root = Path(str(receipt.get("active_root") or target_dir)).resolve()
    if configured_root != active_root:
        raise ValueError("Deployment receipt does not describe the active release root")
    return active_root


def _validated_optional_identity(value: object, pattern: re.Pattern[str], label: str) -> str:
    normalized = str(value or "")
    if normalized and not pattern.fullmatch(normalized):
        raise ValueError(f"Deployment receipt contains an invalid {label}")
    return normalized


def _receipt_identity(receipt: Mapping[str, Any]) -> tuple[str, str]:
    source_commit = _validated_optional_identity(receipt.get("source_commit"), COMMIT_PATTERN, "source commit")
    bundle_hash = _validated_optional_identity(receipt.get("bundle_sha256"), SHA256_PATTERN, "bundle hash")
    return source_commit, bundle_hash


def _missing_install_mismatches(receipt: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"path": path, "reason": "missing-at-install"} for path in _normalized_missing_paths(receipt.get("missing_critical_files"))]


def verify_receipt(target_dir: Path, receipt_path: Path) -> dict[str, Any]:
    receipt = _read_json(receipt_path)
    if receipt.get("schema_version") != 1:
        raise ValueError("Unsupported deployment receipt schema")
    source_commit, bundle_hash = _receipt_identity(receipt)
    expected_hashes = _normalized_hashes(receipt.get("critical_files"), label="critical_files")
    if not expected_hashes:
        raise ValueError("Deployment receipt contains no critical file hashes")

    root = _active_root(target_dir, receipt)
    mismatches = _missing_install_mismatches(receipt) + _verify_hashes(root, expected_hashes)
    return {
        "ok": not mismatches,
        "mode": "quick",
        "active_root": str(root),
        "source_commit": source_commit,
        "bundle_sha256": bundle_hash,
        "checked_file_count": len(expected_hashes),
        "mismatches": mismatches,
    }


def _memory_available_percent() -> float:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return 100.0
    values = _memory_values(lines)
    total = values.get("MemTotal", 0.0)
    return 100.0 if total <= 0.0 else values.get("MemAvailable", 0.0) * 100.0 / total


def _memory_values(lines: list[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in lines:
        key, separator, raw_value = line.partition(":")
        if separator and key in {"MemTotal", "MemAvailable"}:
            values[key] = float(raw_value.strip().split()[0])
    return values


def _resource_gate_active(max_load_per_cpu: float, min_memory_percent: float) -> bool:
    cpu_count = max(1, os.cpu_count() or 1)
    try:
        load_per_cpu = os.getloadavg()[0] / cpu_count
    except OSError:
        load_per_cpu = 0.0
    return load_per_cpu > max_load_per_cpu or _memory_available_percent() < min_memory_percent


def _batches(items: list[tuple[str, str]], size: int) -> Iterator[list[tuple[str, str]]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def _verify_hashes(root: Path, expected_hashes: Mapping[str, str]) -> list[dict[str, str]]:
    mismatches: list[dict[str, str]] = []
    for relative_path, expected_hash in expected_hashes.items():
        candidate = root / relative_path
        if not candidate.is_file():
            mismatches.append({"path": relative_path, "reason": "missing"})
            continue
        actual_hash = _sha256(candidate)
        if actual_hash != expected_hash:
            mismatches.append({"path": relative_path, "reason": "hash-mismatch"})
    return mismatches


def verify_full_manifest(
    root: Path,
    manifest_path: Path,
    *,
    batch_size: int,
    pause_seconds: float,
    max_load_per_cpu: float,
    min_memory_percent: float,
    max_deferrals: int,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    expected_hashes = _normalized_hashes(manifest.get("files"), label="files")
    mismatches: list[dict[str, str]] = []
    deferrals = 0
    checked = 0
    for batch in _batches(sorted(expected_hashes.items()), batch_size):
        while _resource_gate_active(max_load_per_cpu, min_memory_percent):
            deferrals += 1
            if deferrals > max_deferrals:
                raise RuntimeError("Resource gate remained active; full verification deferred")
            time.sleep(pause_seconds)
        mismatches.extend(_verify_hashes(root, dict(batch)))
        checked += len(batch)
        if checked < len(expected_hashes):
            time.sleep(pause_seconds)
    return {
        "ok": not mismatches,
        "mode": "full",
        "active_root": str(root),
        "checked_file_count": checked,
        "resource_deferrals": deferrals,
        "mismatches": mismatches,
    }


def _eligible_manifest_file(path: Path) -> bool:
    return path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"


def _candidate_files(candidate: Path) -> Iterator[Path]:
    if candidate.is_file():
        yield candidate
        return
    if candidate.is_dir():
        yield from (path for path in sorted(candidate.rglob("*")) if _eligible_manifest_file(path))


def _manifest_candidate_paths(root: Path) -> Iterator[Path]:
    for managed_root in MANAGED_ROOTS:
        yield from _candidate_files(root / managed_root)


def create_full_manifest(root: Path, output_path: Path) -> dict[str, Any]:
    excluded_paths = {"deploy/venus/config.venus_evcharger.ini"}
    files: dict[str, str] = {}
    for candidate in _manifest_candidate_paths(root):
        relative_path = candidate.relative_to(root).as_posix()
        if relative_path in excluded_paths or ".bak-" in candidate.name:
            continue
        files[relative_path] = _sha256(candidate)
    payload = {"schema_version": 1, "files": files}
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"ok": True, "mode": "create-manifest", "file_count": len(files), "output_path": str(output_path)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", nargs="?", default="/data/venus-evcharger/dbus-venus-evcharger")
    parser.add_argument("--receipt")
    parser.add_argument("--full-manifest")
    parser.add_argument("--create-manifest")
    parser.add_argument("--manifest-output")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--pause-seconds", type=float, default=0.25)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.75)
    parser.add_argument("--min-memory-percent", type=float, default=15.0)
    parser.add_argument("--max-deferrals", type=int, default=120)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    _validate_runtime_limits(args)
    _validate_manifest_args(args)


def _validate_runtime_limits(args: argparse.Namespace) -> None:
    if args.batch_size < 1:
        raise SystemExit("batch size must be positive")
    if args.pause_seconds <= 0.0:
        raise SystemExit("pause must be positive")
    if args.max_deferrals < 0:
        raise SystemExit("deferrals cannot be negative")


def _validate_manifest_args(args: argparse.Namespace) -> None:
    if args.create_manifest and not args.manifest_output:
        raise SystemExit("--create-manifest requires --manifest-output")


def _verification_result(args: argparse.Namespace, target_dir: Path) -> dict[str, Any]:
    receipt_path = Path(args.receipt) if args.receipt else target_dir / ".bootstrap-state/deployment_receipt.json"
    quick_result = verify_receipt(target_dir, receipt_path)
    if not quick_result["ok"] or not args.full_manifest:
        return quick_result
    return verify_full_manifest(
        Path(str(quick_result["active_root"])),
        Path(args.full_manifest),
        batch_size=args.batch_size,
        pause_seconds=args.pause_seconds,
        max_load_per_cpu=args.max_load_per_cpu,
        min_memory_percent=args.min_memory_percent,
        max_deferrals=args.max_deferrals,
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.create_manifest:
        return create_full_manifest(Path(args.create_manifest).resolve(), Path(args.manifest_output))
    return _verification_result(args, Path(args.target_dir).resolve())


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    try:
        result = _run(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
