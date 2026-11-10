import unittest

from harness import coverage
from harness.router import process_lead
from tests.helpers import Harness


class CoverageReportTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.report = coverage.build_report(self.harness.leads, self.harness.rules,
                                            self.harness.crm, self.harness.providers)

    def test_per_field_coverage_and_cost(self):
        self.assertEqual(self.report["fields"], {
            "work_email": {"needed": 1, "first_provider": 0, "waterfall": 1, "cost": 3},
            "employee_count": {"needed": 7, "first_provider": 4, "waterfall": 6, "cost": 9},
            "industry": {"needed": 7, "first_provider": 5, "waterfall": 6, "cost": 7},
            "hq_country": {"needed": 5, "first_provider": 2, "waterfall": 4, "cost": 6},
            "tech_stack": {"needed": 8, "first_provider": 3, "waterfall": 6, "cost": 21},
        })

    def test_totals(self):
        totals = self.report["totals"]
        self.assertEqual(totals["needed"], 28)
        self.assertEqual(totals["first_provider"], 14)   # 50% with provider 1 alone
        self.assertEqual(totals["waterfall"], 23)        # 82% with the full waterfall
        self.assertEqual(totals["cost"], 46)
        self.assertEqual(totals["enriched_records"], 7)
        self.assertEqual(totals["cost_without_stop"], 63)
        self.assertEqual(totals["saved_by_stop_on_first_match"], 17)
        self.assertEqual(totals["saved_by_known_fields"], 18)
        self.assertEqual(totals["saved_by_disqualified"], 5)

    def test_report_cost_equals_what_the_pipeline_actually_spends(self):
        for lead in self.harness.leads:
            process_lead(lead, self.harness.rules, self.harness.registry, self.harness.model)
        self.assertEqual(self.harness.enrichment.total_cost, self.report["totals"]["cost"])

    def test_building_the_report_writes_nothing(self):
        self.assertEqual(self.harness.crm.write_count, 0)
        self.assertEqual(len(self.harness.crm.contacts), 2)

    def test_formatted_report(self):
        text = coverage.format_report(self.report)
        self.assertIn("ALL FIELDS           28         50%        82%    46", text)
        self.assertIn("cost per enriched record:         6.57", text)
        self.assertIn("saved by stop-on-first-match:     17", text)

    def test_percent_of_nothing(self):
        self.assertEqual(coverage.percent(0, 0), "n/a")


if __name__ == "__main__":
    unittest.main()
