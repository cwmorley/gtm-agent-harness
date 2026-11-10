"""Small text helpers shared by the tools, the validator and the audit log."""

import hashlib
import re


def normalise_email(email: str) -> str:
    """Lower-case, trim, and drop a "+tag" from the local part."""
    email = email.strip().lower()
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def normalise_domain(value: str) -> str:
    """Turn "https://WWW.Example.com/path" into "example.com"."""
    domain = value.strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0]
    domain = domain.split(":", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def normalise_whitespace(text: str) -> str:
    """Collapse every run of whitespace to one space and trim the ends."""
    return " ".join(text.split())


def normalise_label(value: str) -> str:
    """Turn "Healthcare Tech" into "healthcare_tech" so rules can match it."""
    return "_".join(value.strip().lower().split())


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
