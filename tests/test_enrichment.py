import unittest

from harness.enrichment import (
    EnrichmentService,
    FakeEnrichmentProvider,
    enrich_field,
    field_value_is_valid,
    fields_to_enrich,
)
from tests.helpers import Harness


class FieldValidationTests(unittest.TestCase):
    def setUp(self):
        self.rules = Harness().rules

    def valid(self, field, value, domain="acme.example"):
        return field_value_is_valid(field, value, domain, self.rules)

    def test_work_email_needs_valid_syntax_and_the_company_domain(self):
        self.assertTrue(self.valid("work_email", "sam@acme.example"))
        self.assertFalse(self.valid("work_email", "sam@other.example"))
        self.assertFalse(self.valid("work_email", "not an email"))
        self.assertFalse(self.valid("work_email", None))

    def test_employee_count_is_a_positive_integer(self):
        self.assertTrue(self.valid("employee_count", 12))
        for bad in (0, -5, "12", 12.5, True):
            self.assertFalse(self.valid("employee_count", bad), bad)

    def test_industry_country_and_tech_stack(self):
        self.assertTrue(self.valid("industry", "software"))
        self.assertFalse(self.valid("industry", "  "))
        self.assertTrue(self.valid("hq_country", "GB"))
        self.assertFalse(self.valid("hq_country", "United Kingdom"))
        self.assertTrue(self.valid("tech_stack", ["hubspot"]))
        self.assertFalse(self.valid("tech_stack", []))


class WaterfallTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.rules = self.harness.rules
        self.providers = self.harness.providers

    def run_field(self, field, domain, name="x y"):
        return enrich_field(field, domain, name, self.providers, self.rules)

    def test_provider_order_comes_from_the_rules_file(self):
        outcome = self.run_field("tech_stack", "quillstone.example")
        self.assertEqual([a["provider"] for a in outcome["attempts"]],
                         self.rules["enrichment"]["fields"]["tech_stack"]["providers"])

    def test_stops_at_first_valid_value_and_does_not_call_later_providers(self):
        outcome = self.run_field("employee_count", "brightharbor.example")
        self.assertEqual(outcome["value"], 800)
        self.assertEqual(outcome["provider"], "alpha")
        self.assertEqual(len(outcome["attempts"]), 1)
        self.assertEqual(outcome["cost"], 1)

    def test_falls_back_when_first_provider_has_nothing_and_charges_only_the_match(self):
        outcome = self.run_field("employee_count", "quillstone.example")
        self.assertEqual((outcome["value"], outcome["provider"]), (1200, "beta"))
        self.assertEqual(outcome["attempts"], [
            {"provider": "alpha", "returned": False, "valid": False, "cost": 0},
            {"provider": "beta", "returned": True, "valid": True, "cost": 2},
        ])
        self.assertEqual(outcome["cost"], 2)

    def test_value_that_fails_validation_is_charged_then_skipped(self):
        outcome = self.run_field("work_email", "tidewatercoffee.example", "Sam Rivera")
        self.assertEqual(outcome["value"], "sam.rivera@tidewatercoffee.example")
        self.assertEqual(outcome["provider"], "beta")
        self.assertEqual(outcome["attempts"][0],
                         {"provider": "alpha", "returned": True, "valid": False, "cost": 1})
        self.assertEqual(outcome["cost"], 3)

    def test_zero_employee_count_is_rejected_and_next_provider_used(self):
        outcome = self.run_field("employee_count", "ironvale.example")
        self.assertEqual((outcome["value"], outcome["provider"]), (150, "beta"))

    def test_no_provider_has_the_field(self):
        outcome = self.run_field("industry", "mossyoak.example")
        self.assertEqual(outcome["value"], None)
        self.assertEqual(outcome["provider"], None)
        self.assertFalse(outcome["validation_passed"])
        self.assertEqual(outcome["cost"], 0)

    def test_changing_the_order_in_rules_changes_who_supplies_the_value(self):
        self.rules["enrichment"]["fields"]["employee_count"]["providers"] = ["gamma", "alpha"]
        outcome = self.run_field("employee_count", "brightharbor.example")
        self.assertEqual((outcome["value"], outcome["provider"], outcome["cost"]),
                         (805, "gamma", 5))

    def test_service_records_provider_per_field_and_keeps_a_running_cost(self):
        service = self.harness.enrichment
        first = service.enrich("quillstone.example", "Noor Haddad",
                               ["employee_count", "industry", "hq_country", "tech_stack"])
        suppliers = {field: outcome["provider"] for field, outcome in first["fields"].items()}
        self.assertEqual(suppliers, {"employee_count": "beta", "industry": "alpha",
                                     "hq_country": "beta", "tech_stack": "gamma"})
        self.assertEqual(first["cost"], 10)
        service.enrich("brightharbor.example", "Jordan Avery", ["industry"])
        self.assertEqual(service.total_cost, 11)

    def test_a_provider_that_returns_nothing_costs_nothing(self):
        empty = FakeEnrichmentProvider("alpha", 99, {})
        service = EnrichmentService({"alpha": empty, "beta": empty, "gamma": empty}, self.rules)
        result = service.enrich("brightharbor.example", "x y", ["employee_count"])
        self.assertEqual(result["cost"], 0)


class RunConditionTests(unittest.TestCase):
    def test_only_empty_fields_are_enriched(self):
        rules = Harness().rules
        known = {"work_email": "a@b.example", "employee_count": None, "industry": "",
                 "hq_country": "US", "tech_stack": []}
        needed, skipped = fields_to_enrich(known, rules)
        self.assertEqual(needed, ["employee_count", "industry", "tech_stack"])
        self.assertEqual(sorted(skipped), ["hq_country", "work_email"])


if __name__ == "__main__":
    unittest.main()
