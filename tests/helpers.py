"""Shared setup for tests: a fresh fake CRM, providers, registry and fixture leads."""

import copy
import json
import os

from harness.cli import FIXTURES, RULES_PATH, build_fake_model, read_jsonl
from harness.enrichment import EnrichmentService, load_providers
from harness.rules import load_rules
from harness.tools import ReviewStore, build_registry, load_crm

LEADS_PATH = os.path.join(FIXTURES, "leads.jsonl")


class Harness:
    """Everything one test needs, built fresh so tests cannot affect each other."""

    def __init__(self):
        self.rules = load_rules(RULES_PATH)
        self.crm = load_crm(os.path.join(FIXTURES, "crm_contacts.json"))
        self.review_store = ReviewStore()
        self.providers = load_providers(os.path.join(FIXTURES, "providers.json"))
        self.enrichment = EnrichmentService(self.providers, self.rules)
        self.registry = build_registry(self.crm, self.enrichment, self.review_store)
        self.leads = read_jsonl(LEADS_PATH)
        self.model = build_fake_model(self.leads)

    def lead(self, lead_id: str) -> dict:
        for lead in self.leads:
            if lead["lead_id"] == lead_id:
                return copy.deepcopy(lead)
        raise KeyError(lead_id)


def model_override(lead_id: str) -> dict:
    with open(os.path.join(FIXTURES, "model_overrides.json"), encoding="utf-8") as handle:
        return json.load(handle)["overrides"][lead_id]
