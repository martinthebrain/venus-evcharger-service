#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read EV-charger values from the DBus gateway cache without touching DBus."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

DEFAULT_CACHE_PATH = "/run/venus-evcharger/dbus-cache.json"
DEFAULT_SERVICE = "com.victronenergy.evcharger.http_60"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="EV charger paths, for example /Connected or /Mode.")
    parser.add_argument("--cache", default=DEFAULT_CACHE_PATH)
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    return parser


def cached_path_values(cache_path: str, service: str, paths: Sequence[str]) -> tuple[list[str], list[str]]:
    with open(cache_path, encoding="utf-8") as handle:
        snapshot: object = json.load(handle)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("values"), dict):
        raise ValueError("gateway cache has no values object")
    values = snapshot["values"]
    lines: list[str] = []
    missing: list[str] = []
    for path in paths:
        normalized_path = str(path)
        entry = values.get(f"path:{service}{normalized_path}")
        if not isinstance(entry, dict):
            missing.append(normalized_path)
            continue
        lines.append(
            f"{normalized_path}={entry.get('value')!r} "
            f"status={entry.get('status')} age_s={entry.get('age_s')}"
        )
    return lines, missing


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return _print_cached_values(args.cache, args.service, args.paths)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Unable to read gateway cache: {error}")
        return 2


def _print_cached_values(cache_path: str, service: str, paths: Sequence[str]) -> int:
    lines, missing = cached_path_values(cache_path, service, paths)
    for line in lines:
        print(line)
    for path in missing:
        print(f"{path}=<missing>")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
