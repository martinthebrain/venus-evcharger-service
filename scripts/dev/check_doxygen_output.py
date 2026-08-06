#!/usr/bin/env python3
"""Validate completeness and language-independent structure of Doxygen output."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as xml_et
from pathlib import Path
from typing import Any


def _description_text(member: xml_et.Element) -> str:
    parts: list[str] = []
    for tag in ("briefdescription", "detaileddescription"):
        description = member.find(tag)
        if description is not None:
            parts.extend(description.itertext())
    return " ".join(part.strip() for part in parts if part.strip())


def _function_members(xml_directory: Path) -> list[xml_et.Element]:
    members: list[xml_et.Element] = []
    for path in sorted(xml_directory.glob("*.xml")):
        try:
            # Input is trusted local output from this repository's Doxygen run.
            root = xml_et.parse(path).getroot()  # nosec B314
        except xml_et.ParseError as error:
            raise SystemExit(f"Doxygen XML is malformed: {path}: {error}") from error
        members.extend(root.findall(".//memberdef[@kind='function']"))
    return members


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--xml-directory", type=Path, required=True)
    parser.add_argument("--html-index", type=Path, required=True)
    return parser.parse_args()


def _expected_function_counts(manifest_path: Path) -> tuple[int, int, int]:
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_functions = int(manifest["function_count"])
    expected_members = int(manifest["doxygen_member_count"])
    nested_functions = int(manifest["nested_function_count"])
    if expected_functions <= 0:
        raise SystemExit("Doxygen manifest contains no production functions")
    if expected_members <= 0:
        raise SystemExit("Doxygen manifest contains no documentable members")
    if expected_members + nested_functions != expected_functions:
        raise SystemExit("Doxygen manifest function counts are inconsistent")
    return expected_functions, expected_members, nested_functions


def _validate_output_paths(
    html_index: Path,
    xml_directory: Path,
) -> None:
    if not html_index.is_file():
        raise SystemExit(f"Doxygen HTML index is missing: {html_index}")
    if not xml_directory.is_dir():
        raise SystemExit(f"Doxygen XML directory is missing: {xml_directory}")


def _undocumented_names(members: list[xml_et.Element]) -> list[str]:
    return ["".join(member.findtext("name", default="")).strip() for member in members if not _description_text(member)]


def _require_documented_members(members: list[xml_et.Element]) -> None:
    undocumented = _undocumented_names(members)
    if not undocumented:
        return
    preview = ", ".join(name or "<unnamed>" for name in undocumented[:10])
    raise SystemExit(f"Doxygen emitted undocumented functions: {preview}")


def _require_complete_member_count(members: list[xml_et.Element], expected: int) -> None:
    actual = len(members)
    if actual == expected:
        return
    raise SystemExit(f"Doxygen emitted {actual} function members; expected exactly {expected}")


def main() -> int:
    """Fail when generated API documentation is absent or incomplete."""
    args = _parse_args()
    expected_functions, expected_members, nested_functions = _expected_function_counts(args.manifest)
    _validate_output_paths(args.html_index, args.xml_directory)

    members = _function_members(args.xml_directory)
    _require_complete_member_count(members, expected_members)
    _require_documented_members(members)

    print(
        "Doxygen output validated: "
        f"{expected_functions} production functions inventoried; "
        f"{len(members)} code members emitted with descriptions; "
        f"{nested_functions} nested callables retained in the inventory"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
