# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility import path for runtime return-value contracts."""

from __future__ import annotations

from venus_evcharger.core.return_contracts import (
    require_bool,
    require_dict,
    require_float,
    require_float_or_none,
    require_instance,
    require_int,
    require_none,
    require_str,
    require_str_list,
    require_str_or_none,
    require_tuple2,
    require_tuple3,
    require_tuple4,
    require_tuple5,
)


__all__ = [
    "require_bool",
    "require_dict",
    "require_float",
    "require_float_or_none",
    "require_instance",
    "require_int",
    "require_none",
    "require_str",
    "require_str_list",
    "require_str_or_none",
    "require_tuple2",
    "require_tuple3",
    "require_tuple4",
    "require_tuple5",
]
