# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for dependency-free fast-publication binary framing."""

from __future__ import annotations

import plistlib
import struct
import unittest
from unittest.mock import patch

from venus_evcharger.ipc.fast_publication_wire import (
    FAST_PUBLICATION_WIRE_HEADER_BYTES,
    FAST_PUBLICATION_WIRE_MAGIC,
    FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES,
    FAST_PUBLICATION_WIRE_VERSION,
    FastPublicationWireError,
    decode_fast_publication_frame,
    encode_fast_publication_frame,
    fast_publication_frame_size,
)
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
)

_HEADER = struct.Struct("!4sBI")


class FastPublicationWireContractTests(unittest.TestCase):
    def test_round_trip_preserves_supported_transport_values(self) -> None:
        large_order = 1 << 96
        payload = {
            "kind": "publish_evcs_fields",
            PUBLICATION_ORDER_FIELD: large_order,
            PUBLICATION_FIELD_ORDERS_FIELD: {"power": large_order},
            "fields": {
                "none": None,
                "enabled": True,
                "count": 7,
                "power": 1234.5,
                "labels": ["one", "two"],
            },
        }

        frame = encode_fast_publication_frame(payload)

        self.assertEqual(frame[:4], FAST_PUBLICATION_WIRE_MAGIC)
        self.assertEqual(frame[4], FAST_PUBLICATION_WIRE_VERSION)
        self.assertEqual(fast_publication_frame_size(frame), len(frame))
        self.assertEqual(decode_fast_publication_frame(frame), payload)

    def test_header_must_be_complete_versioned_and_bounded(self) -> None:
        self.assertEqual(fast_publication_frame_size(b"EVC"), 0)
        cases = (
            (_HEADER.pack(b"FAIL", 1, 1) + b"x", "invalid-frame-magic"),
            (_HEADER.pack(FAST_PUBLICATION_WIRE_MAGIC, 2, 1) + b"x", "unsupported-frame-version"),
            (_HEADER.pack(FAST_PUBLICATION_WIRE_MAGIC, 1, 0), "empty-frame"),
            (
                _HEADER.pack(
                    FAST_PUBLICATION_WIRE_MAGIC,
                    1,
                    FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES + 1,
                ),
                "frame-too-large",
            ),
        )
        for frame, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                FastPublicationWireError,
                f"^{message}$",
            ):
                fast_publication_frame_size(frame)

    def test_decode_rejects_size_shape_and_invalid_binary_plist(self) -> None:
        frame = encode_fast_publication_frame({"ok": True})
        for malformed in (frame[:-1], frame + b"x"):
            with self.subTest(size=len(malformed)), self.assertRaisesRegex(
                FastPublicationWireError,
                "^frame-size-mismatch$",
            ):
                decode_fast_publication_frame(malformed)

        invalid = _HEADER.pack(FAST_PUBLICATION_WIRE_MAGIC, 1, 1) + b"x"
        with self.assertRaisesRegex(FastPublicationWireError, "^payload-not-decodable$"):
            decode_fast_publication_frame(invalid)

        with patch(
            "venus_evcharger.ipc.fast_publication_wire.plistlib.loads",
            return_value=["not", "a", "mapping"],
        ), self.assertRaisesRegex(FastPublicationWireError, "^payload-must-be-object$"):
            decode_fast_publication_frame(frame)

        with patch(
            "venus_evcharger.ipc.fast_publication_wire.plistlib.loads",
            return_value={1: "not-a-string-key"},
        ), self.assertRaisesRegex(
            FastPublicationWireError,
            "^payload-keys-must-be-strings$",
        ):
            decode_fast_publication_frame(frame)

    def test_encode_rejects_unsupported_and_oversized_payloads(self) -> None:
        with self.assertRaisesRegex(FastPublicationWireError, "^payload-not-encodable$"):
            encode_fast_publication_frame({"unsupported": object()})

        with patch(
            "venus_evcharger.ipc.fast_publication_wire.plistlib.dumps",
            return_value=b"x" * (FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES + 1),
        ), self.assertRaisesRegex(FastPublicationWireError, "^frame-too-large$"):
            encode_fast_publication_frame({"ok": True})

        with patch(
            "venus_evcharger.ipc.fast_publication_wire.plistlib.dumps",
            side_effect=plistlib.InvalidFileException,
        ), self.assertRaisesRegex(
            FastPublicationWireError,
            "^payload-not-encodable$",
        ):
            encode_fast_publication_frame({"ok": True})

    def test_frame_header_size_is_stable(self) -> None:
        self.assertEqual(FAST_PUBLICATION_WIRE_HEADER_BYTES, 9)


if __name__ == "__main__":
    unittest.main()
