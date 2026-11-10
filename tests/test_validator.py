import copy
import unittest

from harness import validator
from tests.helpers import Harness

FORM_TEXT = "We use RouteHive today.  Need new routing ASAP."

GOOD_INTENT = {
    "use_case": {"value": "lead_routing", "quote": "Need new routing ASAP."},
    "urgency": {"value": "high", "quote": "Need new routing ASAP."},
    "competitor": {"value": "RouteHive", "quote": "We use RouteHive today."},
    "summary": {"value": "Wants new routing soon.", "quote": "We use RouteHive today."},
}


def good_plan(rules: dict) -> dict:
    """A plan for a grade A lead that passes every check."""
    return {
        "rules_version": rules["rules_version"],
        "form_text": FORM_TEXT,
        "intent": copy.deepcopy(GOOD_INTENT),
        "model_error": None,
        "disqualifier": None,
        "score": 80,
        "contributions": [{"id": "a", "group": "fit", "met": True, "points": 70},
                          {"id": "b", "group": "intent", "met": True, "points": 10}],
        "grade": "A",
        "actions": ["route_to_ae", "create_task"],
        "owner_id": "9001",
        "properties": {"email": "a@acme.example", "company": "Acme (fictional)",
                       "company_domain": "acme.example", "lead_status": "new",
                       "lead_grade": "A", "lead_score": 80, "owner_id": "9001",
                       "rules_version": rules["rules_version"], "employee_count": 500,
                       "industry": "software"},
        "existing_properties": {},
        "enriched_fields": ["employee_count"],
    }


def checks(flags: list) -> list:
    return [flag["check"] for flag in flags]


class IntentCheckTests(unittest.TestCase):
    def intent_with(self, field, **changes):
        intent = copy.deepcopy(GOOD_INTENT)
        intent[field].update(changes)
        return intent

    def test_good_intent_passes(self):
        self.assertEqual(validator.check_intent(GOOD_INTENT, FORM_TEXT), [])

    def test_schema_not_an_object(self):
        self.assertEqual(checks(validator.check_intent("grade A", FORM_TEXT)), ["intent_schema"])

    def test_schema_extra_key_is_rejected(self):
        intent = dict(GOOD_INTENT, grade="A")
        flags = validator.check_intent(intent, FORM_TEXT)
        self.assertEqual(checks(flags), ["intent_schema"])
        self.assertIn("grade", flags[0]["detail"])

    def test_schema_missing_key_is_rejected(self):
        intent = {k: v for k, v in GOOD_INTENT.items() if k != "urgency"}
        self.assertEqual(checks(validator.check_intent(intent, FORM_TEXT)), ["intent_schema"])

    def test_schema_claim_shape_and_enums(self):
        bad_intents = [
            dict(GOOD_INTENT, urgency="high"),
            self.intent_with("urgency", quote=None),
            self.intent_with("use_case", value="world_domination"),
            self.intent_with("urgency", value="extreme"),
            self.intent_with("competitor", value=""),
            self.intent_with("summary", value=""),
            self.intent_with("summary", value="x" * 301),
        ]
        for intent in bad_intents:
            self.assertEqual(set(checks(validator.check_intent(intent, FORM_TEXT))),
                             {"intent_schema"}, intent)

    def test_quote_must_be_in_the_text(self):
        intent = self.intent_with("urgency", quote="we need this immediately")
        self.assertEqual(checks(validator.check_intent(intent, FORM_TEXT)), ["quote_grounding"])

    def test_quote_match_is_whitespace_normalised_but_otherwise_exact(self):
        spaced = self.intent_with("summary", quote="We use RouteHive today. Need\nnew   routing")
        self.assertEqual(validator.check_intent(spaced, FORM_TEXT), [])
        wrong_case = self.intent_with("summary", quote="we use routehive today.")
        self.assertEqual(checks(validator.check_intent(wrong_case, FORM_TEXT)),
                         ["quote_grounding"])

    def test_a_claim_without_a_quote_is_rejected(self):
        intent = self.intent_with("urgency", quote="")
        self.assertEqual(checks(validator.check_intent(intent, FORM_TEXT)), ["quote_grounding"])

    def test_unknown_values_need_no_quote(self):
        intent = self.intent_with("urgency", value="unknown", quote="")
        intent["competitor"] = {"value": None, "quote": ""}
        self.assertEqual(validator.check_intent(intent, FORM_TEXT), [])

    def test_competitor_must_appear_as_a_whole_word(self):
        partial = self.intent_with("competitor", value="Route", quote="We use RouteHive today.")
        self.assertEqual(checks(validator.check_intent(partial, FORM_TEXT)),
                         ["competitor_grounding"])
        absent = self.intent_with("competitor", value="LeadLoom",
                                  quote="We use RouteHive today.")
        self.assertEqual(checks(validator.check_intent(absent, FORM_TEXT)),
                         ["competitor_grounding"])
        other_case = self.intent_with("competitor", value="routehive")
        self.assertEqual(validator.check_intent(other_case, FORM_TEXT), [])


class PlanCheckTests(unittest.TestCase):
    def setUp(self):
        self.rules = Harness().rules
        self.plan = good_plan(self.rules)

    def failed_checks(self):
        return checks(validator.validate_plan(self.plan, self.rules))

    def test_good_plan_passes(self):
        self.assertEqual(self.failed_checks(), [])

    def test_plan_without_model_output_passes(self):
        self.plan["intent"] = None
        self.assertEqual(self.failed_checks(), [])

    def test_model_error_is_a_flag(self):
        self.plan["intent"] = None
        self.plan["model_error"] = "Anthropic API returned HTTP 529"
        self.assertEqual(self.failed_checks(), ["model_call"])

    def test_bad_intent_fails_the_plan(self):
        self.plan["intent"]["owner"] = "CEO"
        self.assertEqual(self.failed_checks(), ["intent_schema"])

    def test_rules_version_mismatch(self):
        self.plan["rules_version"] = "2025.01.1"
        self.assertEqual(self.failed_checks(), ["rules_version"])

    def test_contributions_must_sum_to_score(self):
        self.plan["contributions"][0]["points"] = 75
        self.assertEqual(self.failed_checks(), ["score_sum"])

    def test_grade_must_match_thresholds(self):
        self.plan["score"] = 60
        self.plan["contributions"][0]["points"] = 50
        self.assertEqual(self.failed_checks(), ["grade_threshold"])

    def test_disqualified_lead_must_carry_the_disqualified_grade(self):
        self.plan["disqualifier"] = "disqualified_industry"
        self.assertEqual(self.failed_checks(), ["grade_threshold"])

    def test_owner_must_be_in_roster(self):
        self.plan["owner_id"] = "CEO"
        self.assertEqual(self.failed_checks(), ["owner_in_roster"])

    def test_routing_grade_needs_an_owner(self):
        self.plan["owner_id"] = None
        self.assertEqual(self.failed_checks(), ["owner_in_roster"])

    def test_action_must_be_allowed_for_the_grade(self):
        self.plan["score"] = 60
        self.plan["contributions"][0]["points"] = 50
        self.plan["grade"] = "B"
        self.assertEqual(self.failed_checks(), ["action_allowed"])
        self.plan["actions"] = ["route_to_sdr"]
        self.assertEqual(self.failed_checks(), [])

    def test_empty_action_list_is_rejected(self):
        self.plan["actions"] = []
        self.assertEqual(self.failed_checks(), ["action_allowed"])

    def test_required_properties_must_be_present(self):
        del self.plan["properties"]["company"]
        self.plan["properties"]["lead_grade"] = ""
        flags = validator.validate_plan(self.plan, self.rules)
        self.assertEqual(checks(flags), ["required_properties"])
        self.assertIn("company", flags[0]["detail"])
        self.assertIn("lead_grade", flags[0]["detail"])

    def test_status_only_may_write_nothing_but_the_status(self):
        self.plan.update({"score": 10, "grade": "D", "actions": ["status_only"],
                          "owner_id": None, "intent": None, "enriched_fields": [],
                          "contributions": [{"id": "a", "group": "fit", "met": True,
                                             "points": 10}]})
        self.plan["properties"] = {"email": "a@acme.example", "lead_status": "unqualified",
                                   "industry": "software"}
        self.assertEqual(self.failed_checks(), ["write_scope"])
        del self.plan["properties"]["industry"]
        self.assertEqual(self.failed_checks(), [])

    def test_enriched_value_may_not_overwrite_a_non_empty_crm_value(self):
        self.plan["enriched_fields"] = ["industry"]
        self.plan["existing_properties"] = {"industry": "logistics"}
        flags = validator.validate_plan(self.plan, self.rules)
        self.assertEqual(checks(flags), ["protected_overwrite"])
        self.assertIn("industry", flags[0]["detail"])

    def test_overwrite_is_allowed_when_rules_mark_the_field_overwritable(self):
        self.assertIn("employee_count", self.rules["crm"]["overwritable_properties"])
        self.plan["existing_properties"] = {"employee_count": 200}
        self.assertEqual(self.failed_checks(), [])
        self.rules["crm"]["overwritable_properties"].remove("employee_count")
        self.assertEqual(self.failed_checks(), ["protected_overwrite"])

    def test_empty_or_equal_crm_values_do_not_block(self):
        self.plan["existing_properties"] = {"industry": "software", "company": ""}
        self.assertEqual(self.failed_checks(), [])

    def test_enriched_value_must_still_pass_its_field_validation(self):
        self.plan["properties"]["employee_count"] = 0
        self.assertEqual(self.failed_checks(), ["enrichment_validation"])

    def test_flag_details_never_repeat_form_text(self):
        self.plan["intent"]["urgency"]["quote"] = "invented quote"
        self.plan["owner_id"] = "CEO"
        for flag in validator.validate_plan(self.plan, self.rules):
            self.assertNotIn("invented quote", flag["detail"])
            self.assertNotIn("RouteHive", flag["detail"])


if __name__ == "__main__":
    unittest.main()
