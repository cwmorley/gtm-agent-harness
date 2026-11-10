import os
import unittest

from harness.cli import RULES_PATH
from harness.rules import (
    assign_owner,
    criterion_met,
    find_disqualifier,
    grade_for_score,
    score_lead,
)
from tests.helpers import Harness

RULES_DOC = os.path.join(os.path.dirname(RULES_PATH), "RULES.md")

STRONG_FACTS = {"employee_count": 800, "industry": "software", "tech_stack": ["hubspot"],
                "business_email": True, "urgency": "high", "use_case": "lead_routing",
                "competitor": "RouteHive", "company_domain": "acme.example"}


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = Harness().rules

    def test_rules_doc_names_the_current_version_and_every_rule_id(self):
        with open(RULES_DOC, encoding="utf-8") as handle:
            doc = handle.read()
        self.assertIn(self.rules["rules_version"], doc)
        for group in ("disqualifiers", "fit_criteria", "intent_criteria"):
            for criterion in self.rules[group]:
                self.assertIn(criterion["id"], doc)

    def test_operators(self):
        facts = {"n": 50, "s": "x", "items": ["a", "b"], "flag": True, "empty": ""}
        self.assertTrue(criterion_met({"field": "n", "op": "between", "value": [50, 60]}, facts))
        self.assertFalse(criterion_met({"field": "n", "op": "between", "value": [51, 60]}, facts))
        self.assertTrue(criterion_met({"field": "s", "op": "in", "value": ["x"]}, facts))
        self.assertTrue(criterion_met({"field": "s", "op": "equals", "value": "x"}, facts))
        self.assertTrue(criterion_met(
            {"field": "items", "op": "contains_any", "value": ["b", "z"]}, facts))
        self.assertTrue(criterion_met({"field": "flag", "op": "is_true"}, facts))
        self.assertTrue(criterion_met({"field": "empty", "op": "is_empty"}, facts))
        self.assertTrue(criterion_met({"field": "s", "op": "not_empty"}, facts))
        self.assertFalse(criterion_met({"field": "missing", "op": "in", "value": ["x"]}, facts))

    def test_unknown_operator_raises(self):
        with self.assertRaises(ValueError):
            criterion_met({"field": "s", "op": "regex", "value": "x"}, {"s": "x"})

    def test_disqualifiers(self):
        self.assertEqual(find_disqualifier({"company_domain": ""}, self.rules),
                         "no_company_domain")
        self.assertEqual(find_disqualifier({"company_domain": "rival-agency.example"},
                                           self.rules), "blocked_domain")
        self.assertEqual(find_disqualifier({"company_domain": "a.example",
                                            "industry": "gambling"}, self.rules),
                         "disqualified_industry")
        self.assertIsNone(find_disqualifier(STRONG_FACTS, self.rules))

    def test_score_is_the_sum_of_met_weights(self):
        scored = score_lead(STRONG_FACTS, self.rules)
        self.assertEqual(scored["score"], 100)
        self.assertEqual(sum(line["points"] for line in scored["contributions"]), 100)
        weak = score_lead({"business_email": True}, self.rules)
        self.assertEqual(weak["score"], 10)
        self.assertEqual([line["id"] for line in weak["contributions"] if line["met"]],
                         ["business_email"])

    def test_grade_boundaries(self):
        expected = {100: "A", 75: "A", 74: "B", 50: "B", 49: "C", 25: "C", 24: "D", 0: "D"}
        for score, grade in expected.items():
            self.assertEqual(grade_for_score(score, self.rules), grade, score)

    def test_intent_points_alone_cannot_reach_grade_b(self):
        intent_total = sum(c["weight"] for c in self.rules["intent_criteria"])
        self.assertEqual(intent_total, 30)
        self.assertLess(intent_total, self.rules["grade_thresholds"]["B"])

    def test_every_grade_has_a_policy_with_sla_and_actions(self):
        for grade in ("A", "B", "C", "D", "DISQUALIFIED"):
            policy = self.rules["grade_policies"][grade]
            self.assertIn("sla_minutes", policy)
            self.assertTrue(policy["actions"])
        self.assertEqual(self.rules["grade_policies"]["A"]["actions"],
                         ["route_to_ae", "create_task"])
        self.assertEqual(self.rules["grade_policies"]["D"]["actions"], ["status_only"])

    def test_owner_by_role_and_territory(self):
        self.assertEqual(assign_owner("A", {"hq_country": "DE"}, None, self.rules)["owner_id"],
                         "9003")
        self.assertEqual(assign_owner("B", {"hq_country": "SG"}, None, self.rules)["owner_id"],
                         "9006")

    def test_unknown_country_falls_back_to_default_territory(self):
        self.assertEqual(assign_owner("A", {"hq_country": ""}, None, self.rules)["owner_id"],
                         "9001")

    def test_existing_crm_owner_is_kept_when_in_roster(self):
        owner = assign_owner("A", {"hq_country": "US"}, "9005", self.rules)
        self.assertEqual(owner, {"owner_id": "9005", "reason": "existing CRM owner kept"})

    def test_existing_owner_not_in_roster_is_replaced(self):
        self.assertEqual(assign_owner("A", {"hq_country": "US"}, "1234", self.rules)["owner_id"],
                         "9001")

    def test_grades_without_owner_role_get_no_owner(self):
        for grade in ("C", "D", "DISQUALIFIED"):
            self.assertIsNone(assign_owner(grade, {}, "9005", self.rules)["owner_id"])


if __name__ == "__main__":
    unittest.main()
