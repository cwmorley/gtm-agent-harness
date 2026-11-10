import json
import os
import tempfile
import unittest

from harness import audit
from harness.normalise import sha256_hex
from harness.router import process_lead
from tests.helpers import Harness


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "logs", "audit.jsonl")
        for lead in self.harness.leads:
            entry = process_lead(lead, self.harness.rules, self.harness.registry,
                                 self.harness.model)
            audit.append(self.path, entry)
        self.entries = audit.read_entries(self.path)

    def test_one_json_line_per_lead(self):
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(len(lines), len(self.harness.leads))
        self.assertEqual([json.loads(line)["lead_id"] for line in lines],
                         [lead["lead_id"] for lead in self.harness.leads])

    def test_append_never_rewrites_earlier_lines(self):
        with open(self.path, encoding="utf-8") as handle:
            before = handle.read()
        audit.append(self.path, {"lead_id": "L-999"})
        with open(self.path, encoding="utf-8") as handle:
            after = handle.read()
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(audit.entries_for_lead(self.path, "L-999")), 1)

    def test_entry_has_the_required_fields(self):
        entry = audit.entries_for_lead(self.path, "L-005")[0]
        self.assertEqual(entry["input_hash"],
                         sha256_hex(json.dumps(self.harness.lead("L-005"), sort_keys=True)))
        self.assertEqual(entry["rules_version"], self.harness.rules["rules_version"])
        self.assertEqual(entry["tools_called"][0],
                         {"tool": "crm_search_contact", "access": "read", "step": "dedupe"})
        self.assertIn({"tool": "crm_upsert", "access": "write", "step": "write"},
                      entry["tools_called"])
        self.assertEqual(entry["enrichment"]["fields"]["tech_stack"]["provider"], "gamma")
        self.assertEqual(entry["enrichment"]["fields"]["tech_stack"]["cost"], 5)
        self.assertTrue(entry["enrichment"]["fields"]["tech_stack"]["validation_passed"])
        self.assertEqual(entry["model"],
                         {"model_id": "fake-scripted-v1", "prompt_version": "intent-v1"})
        self.assertEqual(entry["validator"], {"passed": True, "flags": []})
        self.assertEqual(entry["final_action"], "route_to_ae")
        self.assertGreaterEqual(entry["duration_ms"], 0)
        self.assertIn("timestamp", entry)

    def test_failed_validation_is_recorded_with_flags(self):
        entry = audit.entries_for_lead(self.path, "L-010")[0]
        self.assertFalse(entry["validator"]["passed"])
        self.assertEqual(entry["validator"]["flags"][0]["check"], "quote_grounding")
        self.assertEqual(entry["final_action"], "review")

    def test_no_raw_email_free_text_or_enriched_value_in_the_log(self):
        with open(self.path, encoding="utf-8") as handle:
            log_text = handle.read()
        self.assertNotIn("@", log_text)
        for lead in self.harness.leads:
            self.assertNotIn(lead["email"].lower(), log_text.lower())
            if lead["how_can_we_help"]:
                self.assertNotIn(lead["how_can_we_help"], log_text)
            self.assertNotIn(lead["email"].split("@")[1].lower(), log_text.lower())
        self.assertNotIn("ignore prior instructions", log_text)
        self.assertNotIn("tidewater-mail", log_text)

    def test_hashes_stand_in_for_the_email(self):
        entry = audit.entries_for_lead(self.path, "L-001")[0]
        self.assertEqual(entry["email_hash"], sha256_hex("jordan.avery@brightharbor.example"))

    def test_append_refuses_an_entry_that_contains_an_email_address(self):
        with self.assertRaises(audit.AuditPrivacyError):
            audit.append(self.path, {"lead_id": "L-1", "note": "from sam@acme.example"})
        self.assertEqual(len(audit.read_entries(self.path)), len(self.harness.leads))


if __name__ == "__main__":
    unittest.main()
