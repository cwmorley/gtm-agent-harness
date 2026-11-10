"""Score an intent provider on the golden set.

    python -m evals.run_eval                      # deterministic fake, offline
    python -m evals.run_eval --provider anthropic # real model; needs the two env vars

Three numbers: label accuracy, quote grounding rate, and how many outputs the validator
rejected. Exits 1 when accuracy or grounding is below its threshold.
"""

import argparse
import json
import os
import sys

from harness.model import INTENT_FIELDS, AnthropicProvider, KeywordFakeProvider, ModelError
from harness.normalise import normalise_whitespace
from harness.validator import check_intent, check_intent_schema

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.jsonl")
LABELS = ("use_case", "urgency", "competitor")


def load_golden(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def score_provider(provider, golden: list) -> dict:
    correct_labels = 0
    quotes = 0
    grounded_quotes = 0
    rejected = 0
    for case in golden:
        output = provider.extract_intent(case["text"])
        if check_intent(output, case["text"]):
            rejected += 1
        if check_intent_schema(output):
            continue  # wrong shape: no labels or quotes can be read from it
        text = normalise_whitespace(case["text"])
        for field in INTENT_FIELDS:
            quote = normalise_whitespace(output[field]["quote"])
            if quote:
                quotes += 1
                grounded_quotes += 1 if quote in text else 0
        for label in LABELS:
            if output[label]["value"] == case["expected"][label]:
                correct_labels += 1
    total_labels = len(golden) * len(LABELS)
    return {
        "cases": len(golden),
        "accuracy": correct_labels / total_labels,
        "quote_grounding_rate": grounded_quotes / quotes if quotes else 0.0,
        "rejected": rejected,
    }


def passes(result: dict, min_accuracy: float, min_grounding: float) -> bool:
    return (result["accuracy"] >= min_accuracy
            and result["quote_grounding_rate"] >= min_grounding)


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run_eval")
    parser.add_argument("--provider", choices=("fake", "anthropic"), default="fake")
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    parser.add_argument("--min-grounding", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        if args.provider == "anthropic":
            provider = AnthropicProvider.from_env()
        else:
            provider = KeywordFakeProvider()
        result = score_provider(provider, load_golden(GOLDEN_PATH))
    except ModelError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"provider: {provider.model_id}  cases: {result['cases']}")
    print(f"label accuracy:       {result['accuracy']:.2f}  (minimum {args.min_accuracy:.2f})")
    print(f"quote grounding rate: {result['quote_grounding_rate']:.2f}  "
          f"(minimum {args.min_grounding:.2f})")
    print(f"rejected by validator: {result['rejected']}")
    if passes(result, args.min_accuracy, args.min_grounding):
        print("PASS")
        return 0
    print("FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
