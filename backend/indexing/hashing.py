"""
PMOS S1.5 — Index Fan-Out
hashing.py

Canonical content-hash computation.

The content_hash is the deduplication key and the reconciler's identity check.
It MUST be stable across processes and runs, so it is computed over a
normalized byte representation rather than the raw string:

  1. Unicode NFC normalization (so visually-identical strings hash equally).
  2. Trim trailing whitespace on each line; collapse to '\n' line endings.
  3. Encode UTF-8.
  4. SHA256, lowercase hex.

The hash covers ONLY chunk content, never metadata or ACL. Two chunks with the
same text but different ACLs are the same content (dedup applies), but the
fan-out layer still partitions/filters by ACL at query time — dedup never
collapses across tenants because dedup state is itself tenant-scoped (see
dedup.py).
"""

from __future__ import annotations

import hashlib
import unicodedata

_HASH_NAME = "sha256"
_HASH_HEXLEN = 64


def normalize_for_hash(content: str) -> bytes:
    if not isinstance(content, str):
        raise TypeError("content must be str")
    nfc = unicodedata.normalize("NFC", content)
    lines = nfc.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    trimmed = "\n".join(line.rstrip() for line in lines)
    return trimmed.encode("utf-8")


def content_hash(content: str) -> str:
    """Return the canonical lowercase-hex SHA256 of `content`."""
    digest = hashlib.sha256(normalize_for_hash(content)).hexdigest()
    return digest


def is_valid_hash(value: str) -> bool:
    if not isinstance(value, str) or len(value) != _HASH_HEXLEN:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def verify(content: str, expected_hash: str) -> bool:
    """Constant-time comparison of recomputed hash against expected."""
    actual = content_hash(content)
    # hmac.compare_digest gives constant-time comparison without extra deps.
    import hmac

    return hmac.compare_digest(actual, expected_hash)
