"""Per-field enrichment waterfall.

For each field the rules file lists providers in order. We try them in that order and stop at
the first provider whose value passes the field's validation. A provider is charged whenever it
returns a value, valid or not; a provider that returns nothing costs nothing. Providers here are
fakes loaded from fixtures. This is a from-scratch illustration of the pattern.
"""

import json
import re

from harness.normalise import normalise_domain

EMAIL_PATTERN = re.compile(r"^[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]+$")


def valid_email_for_domain(value, company_domain: str) -> bool:
    if not isinstance(value, str) or not EMAIL_PATTERN.match(value.lower()):
        return False
    return normalise_domain(value.rsplit("@", 1)[1]) == company_domain


def valid_positive_integer(value, company_domain: str) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def valid_non_empty_string(value, company_domain: str) -> bool:
    return isinstance(value, str) and value.strip() != ""


def valid_country_code(value, company_domain: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Z]{2}", value) is not None


def valid_non_empty_list(value, company_domain: str) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(v, str) for v in value)


FIELD_VALIDATORS = {
    "email_matches_domain": valid_email_for_domain,
    "positive_integer": valid_positive_integer,
    "non_empty_string": valid_non_empty_string,
    "country_code": valid_country_code,
    "non_empty_list": valid_non_empty_list,
}


def field_value_is_valid(field: str, value, company_domain: str, rules: dict) -> bool:
    validation_name = rules["enrichment"]["fields"][field]["validation"]
    return FIELD_VALIDATORS[validation_name](value, company_domain)


class FakeEnrichmentProvider:
    """A provider backed by a fixture: {company_domain: {field: value, "people": {...}}}."""

    def __init__(self, name: str, cost_per_match: int, data: dict):
        self.name = name
        self.cost_per_match = cost_per_match
        self.data = data

    def lookup(self, field: str, company_domain: str, full_name: str):
        """Return the provider's value for one field, or None when it has no match."""
        company = self.data.get(company_domain, {})
        if field == "work_email":
            return company.get("people", {}).get(full_name.lower())
        return company.get(field)


def load_providers(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    providers = {}
    for name, entry in raw["providers"].items():
        providers[name] = FakeEnrichmentProvider(name, entry["cost_per_match"], entry["data"])
    return providers


def fields_to_enrich(known: dict, rules: dict) -> tuple[list, dict]:
    """Run condition: only enrich fields the form and the CRM left empty."""
    needed = []
    skipped = {}
    for field in rules["enrichment"]["fields"]:
        if known.get(field) in (None, "", []):
            needed.append(field)
        else:
            skipped[field] = "already known from form or CRM"
    return needed, skipped


def enrich_field(field: str, company_domain: str, full_name: str,
                 providers: dict, rules: dict) -> dict:
    """Walk one field's provider list. Stop at the first value that passes validation."""
    attempts = []
    result = {"value": None, "provider": None, "validation_passed": False}
    for provider_name in rules["enrichment"]["fields"][field]["providers"]:
        provider = providers[provider_name]
        value = provider.lookup(field, company_domain, full_name)
        returned = value is not None
        valid = returned and field_value_is_valid(field, value, company_domain, rules)
        cost = provider.cost_per_match if returned else 0
        attempts.append({"provider": provider_name, "returned": returned,
                         "valid": valid, "cost": cost})
        if valid:
            result = {"value": value, "provider": provider_name, "validation_passed": True}
            break
    result["attempts"] = attempts
    result["cost"] = sum(attempt["cost"] for attempt in attempts)
    return result


class EnrichmentService:
    """What the enrich_company tool calls. Keeps a running cost counter across leads."""

    def __init__(self, providers: dict, rules: dict):
        self.providers = providers
        self.rules = rules
        self.total_cost = 0

    def enrich(self, company_domain: str, full_name: str, fields: list) -> dict:
        results = {}
        for field in fields:
            results[field] = enrich_field(field, company_domain, full_name,
                                          self.providers, self.rules)
        cost = sum(result["cost"] for result in results.values())
        self.total_cost += cost
        return {"fields": results, "cost": cost}
