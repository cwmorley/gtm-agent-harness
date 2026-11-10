import contextlib
import io
import unittest
from unittest import mock

from evals import run_eval
from harness.model import KeywordFakeProvider


class FlawedProvider:
    """Wrong urgency on every case, and an invented urgency quote."""

    model_id = "fake-flawed"

    def extract_intent(self, form_text):
        intent = KeywordFakeProvider().extract_intent(form_text)
        intent["urgency"] = {"value": "high", "quote": "we need this yesterday"}
        return intent


class MalformedProvider:
    model_id = "fake-malformed"

    def extract_intent(self, form_text):
        return {"grade": "A"}


class EvalTests(unittest.TestCase):
    def setUp(self):
        self.golden = run_eval.load_golden(run_eval.GOLDEN_PATH)

    def test_golden_set_has_labels_for_every_case(self):
        self.assertEqual(len(self.golden), 12)
        for case in self.golden:
            self.assertEqual(set(case["expected"]), set(run_eval.LABELS))

    def test_perfect_fake_scores_full_marks(self):
        result = run_eval.score_provider(KeywordFakeProvider(), self.golden)
        self.assertEqual(result, {"cases": 12, "accuracy": 1.0,
                                  "quote_grounding_rate": 1.0, "rejected": 0})
        self.assertTrue(run_eval.passes(result, 0.9, 1.0))

    def test_flawed_fake_loses_accuracy_and_grounding_and_is_rejected(self):
        result = run_eval.score_provider(FlawedProvider(), self.golden)
        self.assertEqual(result["rejected"], 12)
        self.assertAlmostEqual(result["accuracy"], 28 / 36)
        self.assertLess(result["quote_grounding_rate"], 1.0)
        self.assertFalse(run_eval.passes(result, 0.9, 1.0))

    def test_malformed_output_counts_as_rejected_and_wrong(self):
        result = run_eval.score_provider(MalformedProvider(), self.golden)
        self.assertEqual((result["rejected"], result["accuracy"]), (12, 0.0))

    def test_command_exits_zero_for_the_offline_fake(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = run_eval.main([])
        self.assertEqual(code, 0)
        self.assertIn("PASS", output.getvalue())

    def test_command_exits_non_zero_below_threshold(self):
        with mock.patch.object(run_eval, "KeywordFakeProvider", FlawedProvider), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = run_eval.main([])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", output.getvalue())


if __name__ == "__main__":
    unittest.main()
