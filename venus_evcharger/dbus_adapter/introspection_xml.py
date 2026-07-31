# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded XML parsing for gateway-owned DBus introspection responses."""

from __future__ import annotations

import xml.etree.ElementTree as xml_et

DBUS_INTROSPECTION_XML_MAX_BYTES = 256 * 1024
DBUS_INTROSPECTION_XML_MAX_ELEMENTS = 4096
DBUS_INTROSPECTION_XML_MAX_DEPTH = 64
_FORBIDDEN_XML_DECLARATIONS = ("<!DOCTYPE", "<!ENTITY")


def parse_bounded_introspection_xml(xml_data: object) -> xml_et.Element | None:
    """Parse one bounded, declaration-free DBus introspection document."""

    text = _bounded_xml_text(xml_data)
    if text is None or _contains_forbidden_declaration(text):
        return None
    root = _parse_xml(text)
    return root if root is not None and _tree_within_limits(root) else None


def _bounded_xml_text(xml_data: object) -> str | None:
    text = str(xml_data)
    if len(text) > DBUS_INTROSPECTION_XML_MAX_BYTES:
        return None
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(encoded) > DBUS_INTROSPECTION_XML_MAX_BYTES:
        return None
    return text


def _contains_forbidden_declaration(text: str) -> bool:
    upper_text = text.upper()
    return any(declaration in upper_text for declaration in _FORBIDDEN_XML_DECLARATIONS)


def _parse_xml(text: str) -> xml_et.Element | None:
    try:
        # Input is bounded above and rejects DTD/entity declarations.
        return xml_et.fromstring(text)  # nosec B314
    except xml_et.ParseError:
        return None


def _tree_within_limits(root: xml_et.Element) -> bool:
    elements_seen = 0
    pending = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        elements_seen += 1
        if elements_seen > DBUS_INTROSPECTION_XML_MAX_ELEMENTS or depth > DBUS_INTROSPECTION_XML_MAX_DEPTH:
            return False
        pending.extend((child, depth + 1) for child in element)
    return True


__all__ = [
    "DBUS_INTROSPECTION_XML_MAX_BYTES",
    "DBUS_INTROSPECTION_XML_MAX_DEPTH",
    "DBUS_INTROSPECTION_XML_MAX_ELEMENTS",
    "parse_bounded_introspection_xml",
]
