"""Two guards between the validator and the CRM write.

1. Stale state: if the record changed between the dedupe read and the write, do not write.
2. Idempotency: the same form fill, rules version and action must write at most once.
"""

from harness.normalise import sha256_hex


def changed_fields(read_at_dedupe: dict, read_before_write: dict) -> list:
    """Names of fields that differ between the two reads, including added or removed ones."""
    names = set(read_at_dedupe) | set(read_before_write)
    return sorted(name for name in names
                  if read_at_dedupe.get(name) != read_before_write.get(name))


def idempotency_key(input_hash: str, rules_version: str, action: str) -> str:
    return sha256_hex(f"{input_hash}|{rules_version}|{action}")
