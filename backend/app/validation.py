"""Shared validation helpers for untrusted device identifiers."""

from __future__ import annotations

import re

_MAC_COMPACT_RE = re.compile(r"^[0-9A-Fa-f]{12}$")


def normalize_mac_address(value: str) -> str:
    """Validate and return a canonical uppercase colon-delimited MAC address."""
    compact = value.strip().replace(":", "").replace("-", "")
    if not _MAC_COMPACT_RE.fullmatch(compact):
        raise ValueError("Invalid MAC address")
    compact = compact.upper()
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))
