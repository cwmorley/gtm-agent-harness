"""Enrichment coverage and cost report. Read-only: it never writes to the CRM or the audit log.

For every lead it works out which fields needed enrichment, runs the waterfall, and compares
the bill with two what-if bills: no stop-on-first-match, and no conditional skips.
"""

from harness import rules as rules_module
from harness.enrichment import enrich_field, fields_to_enrich
from harness.router import merge_known, read_form


def cost_without_stop(field: str, company_domain: str, full_name: str,
                      providers: dict, rules: dict) -> int:
    """What the field would cost if every listed provider were called every time."""
    cost = 0
    for provider_name in rules["enrichment"]["fields"][field]["providers"]:
        provider = providers[provider_name]
        if provider.lookup(field, company_domain, full_name) is not None:
            cost += provider.cost_per_match
    return cost


def build_report(leads: list, rules: dict, crm, providers: dict) -> dict:
    per_field = {}
    for field in rules["enrichment"]["fields"]:
        per_field[field] = {"needed": 0, "first_provider": 0, "waterfall": 0, "cost": 0}
    totals = {"cost": 0, "cost_without_stop": 0, "enriched_records": 0,
              "saved_by_known_fields": 0, "saved_by_disqualified": 0}

    for payload in leads:
        form = read_form(payload, rules)
        found = crm.search_contact(form["email"], form["company_domain"])
        known = merge_known(form, found, rules)
        full_name = f"{form['first_name']} {form['last_name']}"
        domain = form["company_domain"]
        needed, skipped = fields_to_enrich(known, rules)

        if rules_module.find_disqualifier(known, rules) is not None:
            for field in rules["enrichment"]["fields"]:
                outcome = enrich_field(field, domain, full_name, providers, rules)
                totals["saved_by_disqualified"] += outcome["cost"]
            continue

        for field in skipped:
            outcome = enrich_field(field, domain, full_name, providers, rules)
            totals["saved_by_known_fields"] += outcome["cost"]

        record_enriched = False
        for field in needed:
            outcome = enrich_field(field, domain, full_name, providers, rules)
            line = per_field[field]
            line["needed"] += 1
            line["cost"] += outcome["cost"]
            if outcome["attempts"][0]["valid"]:
                line["first_provider"] += 1
            if outcome["validation_passed"]:
                line["waterfall"] += 1
                record_enriched = True
            totals["cost"] += outcome["cost"]
            totals["cost_without_stop"] += cost_without_stop(field, domain, full_name,
                                                             providers, rules)
        if record_enriched:
            totals["enriched_records"] += 1

    totals["saved_by_stop_on_first_match"] = totals["cost_without_stop"] - totals["cost"]
    totals["needed"] = sum(line["needed"] for line in per_field.values())
    totals["first_provider"] = sum(line["first_provider"] for line in per_field.values())
    totals["waterfall"] = sum(line["waterfall"] for line in per_field.values())
    return {"fields": per_field, "totals": totals}


def percent(part: int, whole: int) -> str:
    if whole == 0:
        return "n/a"
    return f"{round(100 * part / whole)}%"


def format_report(report: dict) -> str:
    lines = [f"{'field':<16}{'needed':>7}{'provider 1':>12}{'waterfall':>11}{'cost':>6}"]
    rows = list(report["fields"].items()) + [("ALL FIELDS", report["totals"])]
    for name, line in rows:
        lines.append(
            f"{name:<16}{line['needed']:>7}"
            f"{percent(line['first_provider'], line['needed']):>12}"
            f"{percent(line['waterfall'], line['needed']):>11}{line['cost']:>6}")
    totals = report["totals"]
    per_record = "n/a"
    if totals["enriched_records"]:
        per_record = f"{totals['cost'] / totals['enriched_records']:.2f}"
    lines += [
        "",
        f"total cost (credits):             {totals['cost']}",
        f"enriched records:                 {totals['enriched_records']}",
        f"cost per enriched record:         {per_record}",
        f"saved by stop-on-first-match:     {totals['saved_by_stop_on_first_match']}",
        f"saved by skipping known fields:   {totals['saved_by_known_fields']}",
        f"saved by skipping disqualified:   {totals['saved_by_disqualified']}",
    ]
    return "\n".join(lines)
