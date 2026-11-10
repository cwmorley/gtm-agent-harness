"""Append-only JSONL audit log: one line per processed lead.

Entries hold hashes, not raw form text or email addresses. append() refuses an entry that
contains anything shaped like an email address.
"""

import json
import os
import re

EMAIL_SHAPE = re.compile(r"[^\s\"@]+@[^\s\"@]+\.[a-zA-Z]{2,}")


class AuditPrivacyError(Exception):
    pass


def append(path: str, entry: dict) -> None:
    line = json.dumps(entry, sort_keys=True)
    if EMAIL_SHAPE.search(line):
        raise AuditPrivacyError("audit entry contains something shaped like an email address")
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_entries(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def entries_for_lead(path: str, lead_id: str) -> list:
    return [entry for entry in read_entries(path) if entry["lead_id"] == lead_id]
