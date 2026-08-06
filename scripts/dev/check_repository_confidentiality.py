#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject tracked field artifacts and environment-specific repository data."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from collections.abc import Iterable
from enum import Enum
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL_PATTERNS_PATH = Path(".git/info/venus-evcharger-confidential-patterns")
POLICY_SOURCE_PATH = Path("scripts/dev/check_repository_confidentiality.py")

FORBIDDEN_TRACKED_PATHS = frozenset(
    {
        "docs/KNOWN_VENUS_OS_ISSUES.md",
        "docs/LEGACY_CONFIG_MIGRATION.md",
        "docs/adr-0001-maintenance-boundaries.md",
    }
)
DOCUMENTATION_NETWORKS = (
    ipaddress.IPv4Network("192.0.2.0/24"),
    ipaddress.IPv4Network("198.51.100.0/24"),
    ipaddress.IPv4Network("203.0.113.0/24"),
)
PUBLIC_VENDOR_ADDRESSES = frozenset(
    {
        ipaddress.IPv4Address("192.168.8.1"),
        ipaddress.IPv4Address("192.168.200.1"),
    }
)
PUBLIC_PRODUCT_DEFAULT_ADDRESSES = frozenset(
    {
        ipaddress.IPv4Address("192.168.1.50"),
    }
)
PRIVATE_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
TAILSCALE_CGNAT = ipaddress.IPv4Network("100.64.0.0/10")
IPV4_PATTERN = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
HOME_PATH_PATTERN = re.compile(r"/home/[^/\s]+/")
INCIDENT_PATH_PATTERN = re.compile(r"(?:^|/)incident-[0-9]{8}(?:-|/)")


class ConfidentialityIssueKind(Enum):
    """Safe, non-secret categories suitable for CI output."""

    HOST_HOME_DIRECTORY = "host-specific home directory"
    LOCAL_CONFIDENTIAL_LITERAL = "locally classified confidential literal"
    NON_PUBLIC_NETWORK_ADDRESS = "non-public network address"
    INTERNAL_DOCUMENT = "internal document must not be tracked"
    FIELD_INCIDENT_ARTIFACT = "field incident artifact must not be tracked"


def tracked_paths(repo: Path) -> tuple[Path, ...]:
    """Return repository-relative paths currently present in the Git index."""

    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    return tuple(Path(item.decode()) for item in result.stdout.split(b"\0") if item)


def local_confidential_patterns(repo: Path) -> tuple[str, ...]:
    """Load optional host-private literals without committing them."""

    path = repo / LOCAL_PATTERNS_PATH
    if not path.is_file():
        return ()
    return tuple(
        line
        for raw_line in path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    )


def is_sensitive_text_surface(path: Path) -> bool:
    """Return whether network examples in this path must be sanitized."""

    return (
        path.suffix.lower() in {".md", ".yml", ".yaml"}
        or path.parts[:1] == (".github",)
        or path.parts[:1] == ("tests",)
        or path.parts[:2] == ("scripts", "dev")
    )


def disallowed_address(address: ipaddress.IPv4Address, *, sensitive_surface: bool) -> bool:
    """Reject private deployment addresses while allowing normative examples."""

    if address in TAILSCALE_CGNAT:
        return True
    if not sensitive_surface or normative_private_address(address):
        return False
    return private_deployment_address(address)


def normative_private_address(address: ipaddress.IPv4Address) -> bool:
    """Return whether one non-public address is an intentional public contract."""

    return address in PUBLIC_VENDOR_ADDRESSES or address in PUBLIC_PRODUCT_DEFAULT_ADDRESSES


def private_deployment_address(address: ipaddress.IPv4Address) -> bool:
    """Return whether one address can identify a private deployment network."""

    if local_only_address(address):
        return False
    if address_in_networks(address, DOCUMENTATION_NETWORKS):
        return False
    return address_in_networks(address, PRIVATE_NETWORKS)


def local_only_address(address: ipaddress.IPv4Address) -> bool:
    """Return whether one address cannot identify a remote deployment."""

    return address.is_loopback or address.is_unspecified


def address_in_networks(
    address: ipaddress.IPv4Address,
    networks: Iterable[ipaddress.IPv4Network],
) -> bool:
    """Return whether an address belongs to any supplied network."""

    return any(address in network for network in networks)


def disallowed_address_tokens(text: str, *, sensitive_surface: bool) -> list[str]:
    """Return each disallowed IPv4 token found in text."""

    addresses: list[str] = []
    for token in IPV4_PATTERN.findall(text):
        try:
            address = ipaddress.IPv4Address(token)
        except ipaddress.AddressValueError:
            continue
        if disallowed_address(address, sensitive_surface=sensitive_surface):
            addresses.append(token)
    return addresses


def contains_local_pattern(text: str, local_patterns: Iterable[str]) -> bool:
    """Return whether text contains a host-private configured literal."""

    lowered = text.casefold()
    return any(pattern.casefold() in lowered for pattern in local_patterns)


def text_issues(
    path: Path,
    text: str,
    local_patterns: Iterable[str],
) -> list[ConfidentialityIssueKind]:
    """Return confidentiality-policy violations in one tracked text file."""

    issues: list[ConfidentialityIssueKind] = []
    if HOME_PATH_PATTERN.search(text):
        issues.append(ConfidentialityIssueKind.HOST_HOME_DIRECTORY)
    if contains_local_pattern(text, local_patterns):
        issues.append(ConfidentialityIssueKind.LOCAL_CONFIDENTIAL_LITERAL)
    address_count = len(
        disallowed_address_tokens(
            text,
            sensitive_surface=is_sensitive_text_surface(path),
        )
    )
    issues.extend(
        ConfidentialityIssueKind.NON_PUBLIC_NETWORK_ADDRESS
        for _index in range(address_count)
    )
    return issues


def tracked_path_issue(path: Path) -> ConfidentialityIssueKind | None:
    """Return a path-level confidentiality failure when one applies."""

    normalized = path.as_posix()
    if normalized in FORBIDDEN_TRACKED_PATHS:
        return ConfidentialityIssueKind.INTERNAL_DOCUMENT
    if INCIDENT_PATH_PATTERN.search(normalized):
        return ConfidentialityIssueKind.FIELD_INCIDENT_ARTIFACT
    return None


def readable_text(path: Path) -> str | None:
    """Read UTF-8 text while skipping missing and binary files."""

    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def confidentiality_issues(
    repo: Path,
    paths: Iterable[Path],
) -> list[ConfidentialityIssueKind]:
    """Return all tracked-path and text confidentiality violations."""

    issues: list[ConfidentialityIssueKind] = []
    local_patterns = local_confidential_patterns(repo)
    for relative_path in paths:
        if relative_path == POLICY_SOURCE_PATH:
            continue
        path_issue = tracked_path_issue(relative_path)
        if path_issue is not None:
            issues.append(path_issue)
            continue
        text = readable_text(repo / relative_path)
        if text is None:
            continue
        issues.extend(text_issues(relative_path, text, local_patterns))
    return issues


def main() -> int:
    """Check the Git index while keeping sensitive details out of CI logs."""

    issues = confidentiality_issues(REPO, tracked_paths(REPO))
    if issues:
        print("Repository confidentiality check failed:", file=sys.stderr)
        observed_kinds = frozenset(issues)
        for known_kind in ConfidentialityIssueKind:
            if known_kind in observed_kinds:
                print(f"- {known_kind.value}", file=sys.stderr)
        print(
            "Potentially sensitive paths and matched text were suppressed.",
            file=sys.stderr,
        )
        return 1
    print("Repository confidentiality contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
