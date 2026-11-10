import unittest

from harness.normalise import (
    normalise_domain,
    normalise_email,
    normalise_label,
    normalise_whitespace,
)
from tests.helpers import Harness


class NormaliseTests(unittest.TestCase):
    def test_email_is_lowercased_trimmed_and_plus_tag_removed(self):
        self.assertEqual(normalise_email("  Dana.Whitlock+demo@NorthPine.example "),
                         "dana.whitlock@northpine.example")

    def test_domain_drops_scheme_www_path_and_port(self):
        self.assertEqual(normalise_domain("https://WWW.Tidewatercoffee.example:443/contact"),
                         "tidewatercoffee.example")
        self.assertEqual(normalise_domain(""), "")

    def test_whitespace_runs_collapse(self):
        self.assertEqual(normalise_whitespace("  a \n\t b  "), "a b")

    def test_label(self):
        self.assertEqual(normalise_label(" Healthcare  Tech "), "healthcare_tech")


class DedupeTests(unittest.TestCase):
    def setUp(self):
        self.crm = Harness().crm

    def test_email_match_ignores_case_and_plus_tag(self):
        found = self.crm.search_contact("DANA.Whitlock+x@northpine.EXAMPLE", "")
        self.assertEqual(found["match"], "email")
        self.assertEqual(found["properties"]["owner_id"], "9002")

    def test_company_domain_match_when_person_is_new(self):
        found = self.crm.search_contact("new.person@kestrelworks.example",
                                        "https://www.kestrelworks.example")
        self.assertEqual(found["match"], "company_domain")
        self.assertEqual(found["properties"]["industry"], "healthcare_tech")

    def test_email_match_wins_over_domain_match(self):
        found = self.crm.search_contact("omar.reyes@kestrelworks.example",
                                        "kestrelworks.example")
        self.assertEqual(found["match"], "email")

    def test_no_match(self):
        found = self.crm.search_contact("nobody@unknown.example", "unknown.example")
        self.assertEqual(found, {"match": "none", "properties": {}})

    def test_search_returns_a_copy_not_the_stored_record(self):
        found = self.crm.search_contact("dana.whitlock@northpine.example", "")
        found["properties"]["industry"] = "changed"
        again = self.crm.search_contact("dana.whitlock@northpine.example", "")
        self.assertEqual(again["properties"]["industry"], "logistics")


if __name__ == "__main__":
    unittest.main()
