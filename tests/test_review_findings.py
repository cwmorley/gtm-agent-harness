"""Regression tests for defects found by an independent review on 2026-11-10.

Each test failed against the code as first written. The fixtures at the time used only clean,
canonical values, which is why the original suite did not catch them.
"""

import unittest

from harness import enrichment, router, tools
from harness import rules as rules_module
from harness import validator

RULES_PATH = "rules/routing_rules.json"


def load_rules():
    return rules_module.load_rules(RULES_PATH)


class EnrichedIndustryNormalisation(unittest.TestCase):
    """an enriched, capitalised disqualifying industry must still disqualify."""

    def test_enriched_capitalised_industry_still_disqualifies(self):
        rules = load_rules()
        providers = {
            "alpha": enrichment.FakeEnrichmentProvider(
                "alpha", 1, {"shady.example": {"industry": "Gambling"}}),
            "beta": enrichment.FakeEnrichmentProvider("beta", 1, {}),
            "gamma": enrichment.FakeEnrichmentProvider("gamma", 1, {}),
        }
        service = enrichment.EnrichmentService(providers, rules)
        registry = tools.build_registry(tools.FakeCRM([]), service, tools.ReviewStore())
        state = {
            "form": {"first_name": "A", "last_name": "B", "company_domain": "shady.example"},
            "known": {"company_domain": "shady.example", "work_email": "x@shady.example",
                      "employee_count": 100, "industry": "", "hq_country": "US",
                      "tech_stack": ["hubspot"]},
            "disqualifier": None,
            "steps": [],
        }
        router.step_enrich(state, rules, registry)
        self.assertEqual(
            state["disqualifier"], "disqualified_industry",
            "enriched industry 'Gambling' slipped past the disqualifier because it is stored raw")


class EmployeeCountCoercion(unittest.TestCase):
    """a string employee_count must not crash scoring."""

    def test_string_employee_count_does_not_crash_scoring(self):
        rules = load_rules()
        form = router.read_form(
            {"lead_id": "L-x", "email": "a@acme.example", "employee_count": "500"}, rules)
        # Currently raises TypeError inside the 'between' comparison.
        scored = rules_module.score_lead(dict(form), rules)
        self.assertEqual(
            scored["score"], 35,
            "employee_count '500' should score employee_count_in_range (25) + business_email (10)")


class BlockedSubdomainEvasion(unittest.TestCase):
    """a subdomain of a blocked domain must be disqualified."""

    def test_subdomain_of_blocked_domain_is_disqualified(self):
        rules = load_rules()
        facts = {"company_domain": "mail.rival-agency.example"}
        self.assertEqual(
            rules_module.find_disqualifier(facts, rules), "blocked_domain",
            "a subdomain of a blocked domain evaded the exact-match block list")


class IntentGradeReach(unittest.TestCase):
    """Intent points can move a 45-49 fit score from C to A. Pinned so the README stays true."""

    def test_full_intent_points_lift_a_borderline_c_to_a(self):
        rules = load_rules()
        fit_facts = {"employee_count": 100, "industry": "software"}  # 25 + 20 = 45
        self.assertEqual(rules_module.score_lead(fit_facts, rules)["score"], 45)
        self.assertEqual(rules_module.grade_for_score(45, rules), "C")
        with_intent = dict(fit_facts, urgency="high", use_case="lead_routing",
                           competitor="RouteHive")
        self.assertEqual(rules_module.score_lead(with_intent, rules)["score"], 75)
        # 45 fit + 30 intent = 75, which is grade A. The README states this limit.
        self.assertEqual(rules_module.grade_for_score(75, rules), "A")


class CompanyReuseFromCrm(unittest.TestCase):
    """a returning contact's company name should be reused from the CRM when blank."""

    def test_blank_company_is_filled_from_crm_on_email_match(self):
        rules = load_rules()
        form = router.read_form({"email": "a@acme.example", "company": ""}, rules)
        found = {"match": "email",
                 "properties": {"company": "Acme (fictional)", "company_domain": "acme.example"}}
        known = router.merge_known(form, found, rules)
        self.assertEqual(
            known["company"], "Acme (fictional)",
            "merge_known did not reuse the CRM company, so the lead would be flagged to review")


class EmailShapedCompetitor(unittest.TestCase):
    """an email-shaped competitor must be rejected before it reaches CRM/audit."""

    def test_email_shaped_competitor_is_rejected(self):
        form_text = "We may just email sales@rival.example instead of buying."
        intent = {"competitor": {"value": "sales@rival.example",
                                 "quote": "We may just email sales@rival.example instead."}}
        flags = validator.check_competitor_grounding(intent, form_text)
        self.assertTrue(
            flags,
            "an email-shaped competitor value was accepted; it would be written to the CRM and "
            "then crash the audit privacy guard after the write")


if __name__ == "__main__":
    unittest.main()
