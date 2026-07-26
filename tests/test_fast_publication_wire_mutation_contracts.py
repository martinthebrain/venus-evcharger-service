# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-resistant boundary contracts for fast-publication wire frames."""

from __future__ import annotations

import struct
import unittest

from venus_evcharger.ipc.fast_publication_wire import (
    FAST_PUBLICATION_WIRE_HEADER_BYTES,
    FAST_PUBLICATION_WIRE_MAGIC,
    FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES,
    FAST_PUBLICATION_WIRE_VERSION,
    FastPublicationWireError,
    decode_fast_publication_frame,
    encode_fast_publication_frame,
    fast_publication_frame_size,
    fast_publication_payload_limit_reason,
)
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
)
from venus_evcharger.ipc.publication_payload import (
    MAX_PUBLICATION_COALESCE_KEY_BYTES,
    MAX_PUBLICATION_FIELD_NAME_BYTES,
    MAX_PUBLICATION_FIELDS_PER_KEY,
    MAX_PUBLICATION_PAYLOAD_BYTES,
)

_HEADER = struct.Struct("!4sBI")


class FastPublicationWireMutationContracts(unittest.TestCase):
    def test_encoded_header_describes_the_exact_binary_body(self) -> None:
        payload = {"kind": "publish_evcs_fields", "fields": {"power": 2300.5}}

        frame = encode_fast_publication_frame(payload)
        magic, version, body_size = _HEADER.unpack_from(frame)

        self.assertEqual(FAST_PUBLICATION_WIRE_HEADER_BYTES, _HEADER.size)
        self.assertEqual(magic, FAST_PUBLICATION_WIRE_MAGIC)
        self.assertEqual(version, FAST_PUBLICATION_WIRE_VERSION)
        self.assertEqual(body_size, len(frame) - FAST_PUBLICATION_WIRE_HEADER_BYTES)
        self.assertEqual(fast_publication_frame_size(frame), len(frame))
        self.assertEqual(decode_fast_publication_frame(frame), payload)

    def test_order_roundtrip_normalizes_only_decimal_integer_values(self) -> None:
        cases: tuple[tuple[object, object], ...] = (
            (0, 0),
            (-1, -1),
            (1 << 96, 1 << 96),
            ("001", 1),
            ("-0042", -42),
            ("+42", "+42"),
            ("1.0", "1.0"),
            (" 1", " 1"),
            ("-", "-"),
            (True, True),
            (None, None),
        )

        for original, expected in cases:
            with self.subTest(original=original):
                payload = {
                    PUBLICATION_ORDER_FIELD: original,
                    PUBLICATION_FIELD_ORDERS_FIELD: {"power": original},
                }
                decoded = decode_fast_publication_frame(encode_fast_publication_frame(payload))
                self.assertEqual(decoded[PUBLICATION_ORDER_FIELD], expected)
                self.assertEqual(
                    decoded[PUBLICATION_FIELD_ORDERS_FIELD],
                    {"power": expected},
                )

    def test_missing_and_non_mapping_order_metadata_remains_missing_or_unchanged(self) -> None:
        payload_without_order = {"kind": "publish_evcs_fields"}
        decoded_without_order = decode_fast_publication_frame(
            encode_fast_publication_frame(payload_without_order)
        )
        self.assertEqual(decoded_without_order, payload_without_order)
        self.assertNotIn(PUBLICATION_ORDER_FIELD, decoded_without_order)

        payload_with_scalar_field_orders = {
            PUBLICATION_ORDER_FIELD: "not-decimal",
            PUBLICATION_FIELD_ORDERS_FIELD: "not-a-mapping",
        }
        self.assertEqual(
            decode_fast_publication_frame(
                encode_fast_publication_frame(payload_with_scalar_field_orders)
            ),
            payload_with_scalar_field_orders,
        )

    def test_coalesce_key_limit_is_utf8_byte_exact(self) -> None:
        exact_ascii = "x" * MAX_PUBLICATION_COALESCE_KEY_BYTES
        exact_multibyte = "ä" * (MAX_PUBLICATION_COALESCE_KEY_BYTES // 2)

        self.assertEqual(
            fast_publication_payload_limit_reason({"coalesce_key": exact_ascii}),
            "",
        )
        self.assertEqual(
            fast_publication_payload_limit_reason({"coalesce_key": exact_multibyte}),
            "",
        )
        self.assertEqual(
            fast_publication_payload_limit_reason({"coalesce_key": f"{exact_multibyte}x"}),
            "coalesce-key-too-large",
        )

    def test_field_count_and_utf8_name_limits_are_inclusive(self) -> None:
        exact_fields = {f"f{index}": index for index in range(MAX_PUBLICATION_FIELDS_PER_KEY)}
        too_many_fields = {**exact_fields, "overflow": True}
        exact_multibyte_name = "ä" * (MAX_PUBLICATION_FIELD_NAME_BYTES // 2)

        self.assertEqual(
            fast_publication_payload_limit_reason({"fields": exact_fields}),
            "",
        )
        self.assertEqual(
            fast_publication_payload_limit_reason({"fields": too_many_fields}),
            "field-limit",
        )
        self.assertEqual(
            fast_publication_payload_limit_reason({"fields": {exact_multibyte_name: 1}}),
            "",
        )
        self.assertEqual(
            fast_publication_payload_limit_reason(
                {"fields": {f"{exact_multibyte_name}x": 1}}
            ),
            "field-name-too-large",
        )

    def test_binary_payload_limit_is_inclusive_and_reports_encoding_failures(self) -> None:
        exact_payload = {"blob": "x" * 49_141}
        oversized_payload = {"blob": "x" * 49_142}

        exact_frame = encode_fast_publication_frame(exact_payload)
        self.assertEqual(
            len(exact_frame) - FAST_PUBLICATION_WIRE_HEADER_BYTES,
            MAX_PUBLICATION_PAYLOAD_BYTES,
        )
        self.assertEqual(fast_publication_payload_limit_reason(exact_payload), "")
        self.assertEqual(
            fast_publication_payload_limit_reason(oversized_payload),
            "payload-limit",
        )
        self.assertEqual(
            fast_publication_payload_limit_reason({"unsupported": object()}),
            "payload-not-encodable",
        )

    def test_payload_limit_reasons_have_a_stable_first_failure_order(self) -> None:
        oversized_key = "x" * (MAX_PUBLICATION_COALESCE_KEY_BYTES + 1)
        oversized_fields = {
            f"f{index}": index for index in range(MAX_PUBLICATION_FIELDS_PER_KEY + 1)
        }
        payload = {
            "coalesce_key": oversized_key,
            "fields": oversized_fields,
            "unsupported": object(),
        }

        self.assertEqual(
            fast_publication_payload_limit_reason(payload),
            "coalesce-key-too-large",
        )
        payload.pop("coalesce_key")
        self.assertEqual(fast_publication_payload_limit_reason(payload), "field-limit")
        payload["fields"] = {}
        self.assertEqual(
            fast_publication_payload_limit_reason(payload),
            "payload-not-encodable",
        )

    def test_frame_body_size_accepts_exact_limit_and_rejects_adjacent_values(self) -> None:
        exact_header = _HEADER.pack(
            FAST_PUBLICATION_WIRE_MAGIC,
            FAST_PUBLICATION_WIRE_VERSION,
            FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES,
        )
        self.assertEqual(
            fast_publication_frame_size(exact_header),
            FAST_PUBLICATION_WIRE_HEADER_BYTES
            + FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES,
        )

        for body_size, message in (
            (0, "empty-frame"),
            (FAST_PUBLICATION_WIRE_MAX_PAYLOAD_BYTES + 1, "frame-too-large"),
        ):
            with self.subTest(body_size=body_size), self.assertRaisesRegex(
                FastPublicationWireError,
                f"^{message}$",
            ):
                fast_publication_frame_size(
                    _HEADER.pack(
                        FAST_PUBLICATION_WIRE_MAGIC,
                        FAST_PUBLICATION_WIRE_VERSION,
                        body_size,
                    )
                )

    def test_complete_header_requires_exact_magic_and_version(self) -> None:
        self.assertEqual(
            fast_publication_frame_size(b"x" * (FAST_PUBLICATION_WIRE_HEADER_BYTES - 1)),
            0,
        )
        for magic, version, message in (
            (b"EVCG", FAST_PUBLICATION_WIRE_VERSION, "invalid-frame-magic"),
            (
                FAST_PUBLICATION_WIRE_MAGIC,
                FAST_PUBLICATION_WIRE_VERSION + 1,
                "unsupported-frame-version",
            ),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(
                FastPublicationWireError,
                f"^{message}$",
            ):
                fast_publication_frame_size(_HEADER.pack(magic, version, 1))


if __name__ == "__main__":
    unittest.main()
