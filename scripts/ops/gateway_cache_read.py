#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read semantic EV-charger diagnostics without knowing the gateway transport."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from venus_evcharger.ipc.gateway_diagnostics import (
    DEFAULT_GATEWAY_DIAGNOSTICS_PATH,
    GatewayDiagnosticsFileReader,
)
from venus_evcharger.ports.gateway_diagnostic_values import GatewayDiagnosticSample
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsUnavailable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fields",
        nargs="*",
        help="Semantic fields such as operating_mode, charging_enabled, or ac_power_w; all when omitted.",
    )
    parser.add_argument("--snapshot", default=DEFAULT_GATEWAY_DIAGNOSTICS_PATH)
    return parser


def diagnostic_field_values(snapshot_path: str, fields: Sequence[str]) -> list[str]:
    snapshot = GatewayDiagnosticsFileReader(snapshot_path).read_snapshot()
    samples: dict[str, GatewayDiagnosticSample] = {
        sample.name: sample for sample in snapshot.ev_charger
    }
    selected = _selected_fields(fields, samples)
    return [_sample_line(name, samples[name]) for name in selected]


def _selected_fields(
    fields: Sequence[str], samples: dict[str, GatewayDiagnosticSample]
) -> list[str]:
    selected = list(fields) if fields else sorted(samples)
    for name in selected:
        if name not in samples:
            raise ValueError(f"unknown semantic diagnostic field: {name}")
    return selected


def _sample_line(name: str, sample: GatewayDiagnosticSample) -> str:
    return (
        f"{name}={sample.value!r} status={sample.status} "
        f"applicability={sample.applicability} changed_at={sample.changed_at} "
        f"confirmed_at={sample.confirmed_at} confidence={sample.confidence} "
        f"reason_code={sample.reason_code!r}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        lines = diagnostic_field_values(args.snapshot, args.fields)
    except (GatewayDiagnosticsUnavailable, TypeError, ValueError) as error:
        print(f"Unable to read gateway diagnostics: {error}")
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
