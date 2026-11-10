import contextlib
import io
import os
import tempfile
import unittest
import unittest.mock

from harness import audit
from harness.cli import FIXTURES, main

LEADS = os.path.join(FIXTURES, "leads.jsonl")
RESUBMISSION = os.path.join(FIXTURES, "resubmission.jsonl")

EXPECTED_ROWS = {
    "L-001": ("none", "A", "95", "route_to_ae", "9001", "pass", "-"),
    "L-002": ("email", "B", "65", "route_to_sdr", "9002", "pass", "-"),
    "L-003": ("none", "C", "25", "nurture", "-", "pass", "-"),
    "L-004": ("none", "DISQUALIFIED", "0", "status_only", "-", "pass", "disqualified_industry"),
    "L-005": ("none", "A", "95", "route_to_ae", "9003", "pass", "-"),
    "L-006": ("none", "C", "35", "review", "-", "FAIL", "intent_schema"),
    "L-007": ("none", "B", "70", "route_to_sdr", "9004", "pass", "-"),
    "L-008": ("company_domain", "A", "95", "route_to_ae", "9005", "pass", "-"),
    "L-009": ("none", "D", "10", "status_only", "-", "pass", "-"),
    "L-010": ("none", "B", "70", "review", "9002", "FAIL", "quote_grounding"),
}


def run_cli(arguments: list) -> tuple:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.audit_log = os.path.join(directory.name, "audit.jsonl")

    def test_run_prints_one_row_per_fixture_lead_and_totals(self):
        code, output, _ = run_cli(["run", LEADS, "--audit-log", self.audit_log])
        self.assertEqual(code, 0)
        rows = {line.split()[0]: tuple(line.split()[1:]) for line in output.splitlines()
                if line.startswith("L-")}
        self.assertEqual(rows, EXPECTED_ROWS)
        self.assertIn("leads: 10  crm writes: 11 (8 contacts, 3 tasks)  review queue: 2  "
                      "replays: 0  enrichment cost: 46 credits", output)
        self.assertEqual(len(audit.read_entries(self.audit_log)), 10)

    def test_resubmitted_form_fill_is_one_write_and_one_replay_audit_entry(self):
        code, output, _ = run_cli(["run", LEADS, RESUBMISSION, "--audit-log", self.audit_log])
        self.assertEqual(code, 0)
        self.assertIn("leads: 11  crm writes: 11 (8 contacts, 3 tasks)", output)
        self.assertIn("replays: 1", output)
        entries = audit.entries_for_lead(self.audit_log, "L-001")
        self.assertEqual([entry["write_guards"]["replay"] for entry in entries], [False, True])
        self.assertEqual([entry["final_action"] for entry in entries],
                         ["route_to_ae", "replay_no_write"])

    def test_run_leaves_no_email_address_in_the_audit_log(self):
        run_cli(["run", LEADS, "--audit-log", self.audit_log])
        with open(self.audit_log, encoding="utf-8") as handle:
            self.assertNotIn("@", handle.read())

    def test_explain_prints_the_reasoning_for_one_lead(self):
        run_cli(["run", LEADS, "--audit-log", self.audit_log])
        code, output, _ = run_cli(["explain", "L-006", "--audit-log", self.audit_log])
        self.assertEqual(code, 0)
        self.assertIn("score 35 -> grade C", output)
        self.assertIn("validator: FAIL", output)
        self.assertIn("flag intent_schema: unexpected keys ['grade', 'owner']", output)
        self.assertIn("final action: review", output)
        code, output, _ = run_cli(["explain", "L-005", "--audit-log", self.audit_log])
        self.assertIn("tech_stack      from gamma  cost 5  tried beta:miss, gamma:valid", output)
        self.assertIn("crm_upsert          write  in step write", output)
        self.assertIn("POST /crm/v3/objects/contacts/batch/upsert", output)

    def test_explain_unknown_lead_exits_non_zero(self):
        run_cli(["run", LEADS, "--audit-log", self.audit_log])
        code, _, errors = run_cli(["explain", "L-404", "--audit-log", self.audit_log])
        self.assertEqual(code, 1)
        self.assertIn("no audit entry", errors)

    def test_coverage_command(self):
        code, output, _ = run_cli(["coverage", LEADS])
        self.assertEqual(code, 0)
        self.assertIn("ALL FIELDS           28         50%        82%    46", output)

    def test_live_flag_refuses_and_processes_nothing(self):
        code, output, errors = run_cli(["run", LEADS, "--live", "--audit-log", self.audit_log])
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("not implemented in this demo", errors)
        self.assertFalse(os.path.exists(self.audit_log))

    def test_real_provider_without_configuration_exits_before_any_call(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            code, _, errors = run_cli(["run", LEADS, "--provider", "anthropic",
                                       "--audit-log", self.audit_log])
        self.assertEqual(code, 2)
        self.assertIn("ANTHROPIC_MODEL", errors)


if __name__ == "__main__":
    unittest.main()
