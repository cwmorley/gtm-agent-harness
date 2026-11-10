"""Command line: run a file of form fills, explain one lead, or report enrichment coverage."""

import argparse
import json
import os
import sys

from harness import audit, coverage, crm_hubspot
from harness.enrichment import EnrichmentService, load_providers
from harness.model import AnthropicProvider, KeywordFakeProvider, ModelError, ScriptedProvider
from harness.router import process_lead
from harness.rules import load_rules
from harness.tools import ReviewStore, build_registry, load_crm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(REPO_ROOT, "rules", "routing_rules.json")
FIXTURES = os.path.join(REPO_ROOT, "fixtures")
DEFAULT_AUDIT_LOG = os.path.join("out", "audit.jsonl")


def read_jsonl(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_fake_model(leads: list) -> ScriptedProvider:
    """The keyword fake, plus canned misbehaviour for the leads named in model_overrides.json."""
    with open(os.path.join(FIXTURES, "model_overrides.json"), encoding="utf-8") as handle:
        overrides = json.load(handle)["overrides"]
    outputs_by_text = {}
    for lead in leads:
        if lead["lead_id"] in overrides:
            outputs_by_text[lead["how_can_we_help"]] = overrides[lead["lead_id"]]
    return ScriptedProvider(outputs_by_text, KeywordFakeProvider())


def flag_names(entry: dict) -> str:
    names = [flag["check"] for flag in entry["validator"]["flags"]]
    if entry["write_guards"]["record_changed"]:
        names.append("record_changed")
    if entry["disqualifier"]:
        names.append(entry["disqualifier"])
    return ",".join(dict.fromkeys(names)) or "-"


def format_table(entries: list) -> str:
    template = "{:<6} {:<15} {:<13} {:>5}  {:<16} {:<6} {:<9} {}"
    lines = [template.format("lead", "dedupe", "grade", "score", "action", "owner",
                             "validator", "flags")]
    for entry in entries:
        lines.append(template.format(
            entry["lead_id"], entry["dedupe"], entry["grade"], entry["score"],
            entry["final_action"], entry["owner_id"] or "-",
            "pass" if entry["validator"]["passed"] else "FAIL", flag_names(entry)))
    return "\n".join(lines)


def format_totals(entries: list, crm, review_store, enrichment_service) -> str:
    replays = sum(1 for entry in entries if entry["write_guards"]["replay"])
    return (f"leads: {len(entries)}  crm writes: {crm.write_count} "
            f"({crm.write_count - len(crm.tasks)} contacts, {len(crm.tasks)} tasks)  "
            f"review queue: {len(review_store.records)}  replays: {replays}  "
            f"enrichment cost: {enrichment_service.total_cost} credits")


def command_run(args) -> int:
    if args.live:
        crm_hubspot.send_live()
    rules = load_rules(RULES_PATH)
    leads = []
    for path in args.leads_files:
        leads += read_jsonl(path)
    if args.provider == "anthropic":
        model = AnthropicProvider.from_env()
    else:
        model = build_fake_model(leads)
    crm = load_crm(os.path.join(FIXTURES, "crm_contacts.json"))
    review_store = ReviewStore()
    enrichment_service = EnrichmentService(
        load_providers(os.path.join(FIXTURES, "providers.json")), rules)
    registry = build_registry(crm, enrichment_service, review_store)

    entries = []
    for payload in leads:
        entry = process_lead(payload, rules, registry, model)
        audit.append(args.audit_log, entry)
        entries.append(entry)
    print(format_table(entries))
    print()
    print(format_totals(entries, crm, review_store, enrichment_service))
    print(f"audit log: {args.audit_log} (rules_version {rules['rules_version']})")
    return 0


def format_explanation(entry: dict) -> str:
    lines = [
        f"lead {entry['lead_id']}  at {entry['timestamp']}  rules_version {entry['rules_version']}",
        f"input hash: {entry['input_hash']}",
        f"dedupe: {entry['dedupe']}    disqualifier: {entry['disqualifier'] or 'none'}",
        "steps:",
    ]
    for step in entry["steps"]:
        lines.append(f"  {step['step']:<10}{step['status']:<9}{step['note']}")
    lines.append("tools called:")
    for call in entry["tools_called"]:
        lines.append(f"  {call['tool']:<20}{call['access']:<7}in step {call['step']}")
    lines.append(f"enrichment (cost {entry['enrichment']['cost']} credits):")
    for field, outcome in entry["enrichment"]["fields"].items():
        tried = ", ".join(
            f"{a['provider']}:{'valid' if a['valid'] else 'invalid' if a['returned'] else 'miss'}"
            for a in outcome["attempts"])
        lines.append(f"  {field:<16}from {outcome['provider'] or 'nobody':<7}"
                     f"cost {outcome['cost']}  tried {tried}")
    for field, reason in entry["enrichment"]["skipped"].items():
        lines.append(f"  {field:<16}skipped: {reason}")
    if entry["model"]:
        lines.append(f"model: {entry['model']['model_id']}  "
                     f"prompt {entry['model']['prompt_version']}")
    if entry["intent"]:
        intent = entry["intent"]
        lines.append(f"intent: use_case={intent['use_case']} urgency={intent['urgency']} "
                     f"competitor={intent['competitor']}")
    lines.append(f"score {entry['score']} -> grade {entry['grade']}")
    for line in entry["contributions"]:
        mark = "+" if line["met"] else " "
        lines.append(f"  {mark} {line['points']:>3}  {line['group']:<7}{line['id']}")
    lines.append(f"planned actions: {entry['planned_actions']}  "
                 f"owner: {entry['owner_id'] or '-'} ({entry['owner_reason']})  "
                 f"SLA minutes: {entry['sla_minutes']}")
    lines.append(f"validator: {'pass' if entry['validator']['passed'] else 'FAIL'}")
    for flag in entry["validator"]["flags"]:
        lines.append(f"  flag {flag['check']}: {flag['detail']}")
    guards = entry["write_guards"]
    lines.append(f"write guards: replay={guards['replay']} "
                 f"record_changed={guards['record_changed'] or 'no'}")
    if entry["crm_request"]:
        request = entry["crm_request"]
        lines.append(f"CRM request (built, not sent): {request['method']} {request['path']}")
        lines.append(f"  properties: {', '.join(request['property_names'])}")
    lines.append(f"final action: {entry['final_action']}  ({entry['duration_ms']} ms)")
    return "\n".join(lines)


def command_explain(args) -> int:
    entries = audit.entries_for_lead(args.audit_log, args.lead_id)
    if not entries:
        print(f"no audit entry for lead {args.lead_id} in {args.audit_log}", file=sys.stderr)
        return 1
    print("\n\n".join(format_explanation(entry) for entry in entries))
    return 0


def command_coverage(args) -> int:
    rules = load_rules(RULES_PATH)
    crm = load_crm(os.path.join(FIXTURES, "crm_contacts.json"))
    providers = load_providers(os.path.join(FIXTURES, "providers.json"))
    report = coverage.build_report(read_jsonl(args.leads_file), rules, crm, providers)
    print(coverage.format_report(report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m harness")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="process one or more JSONL files of form fills")
    run.add_argument("leads_files", nargs="+")
    run.add_argument("--audit-log", default=DEFAULT_AUDIT_LOG)
    run.add_argument("--provider", choices=("fake", "anthropic"), default="fake")
    run.add_argument("--live", action="store_true",
                     help="send to HubSpot (not implemented in this demo; exits with an error)")
    run.set_defaults(handler=command_run)

    explain = commands.add_parser("explain", help="show the audit trail for one lead")
    explain.add_argument("lead_id")
    explain.add_argument("--audit-log", default=DEFAULT_AUDIT_LOG)
    explain.set_defaults(handler=command_explain)

    cover = commands.add_parser("coverage", help="enrichment coverage and cost report")
    cover.add_argument("leads_file")
    cover.set_defaults(handler=command_coverage)
    return parser


def main(argv: list) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (crm_hubspot.LiveWriteNotImplementedError, ModelError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
