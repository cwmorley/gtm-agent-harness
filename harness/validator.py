"""Checks that run before any CRM write. Every check returns flags; no flags means pass.

A flag is {"check": <id>, "detail": <text>}. Details never contain form text or email
addresses, because flags are copied into the audit log.
"""

import re

from harness.enrichment import field_value_is_valid
from harness.model import INTENT_FIELDS, URGENCIES, USE_CASES
from harness.normalise import normalise_whitespace
from harness.rules import DISQUALIFIED, grade_for_score, is_empty, roster_ids

MAX_SUMMARY_LENGTH = 300


def flag(check: str, detail: str) -> dict:
    return {"check": check, "detail": detail}


def check_intent_schema(intent) -> list:
    if not isinstance(intent, dict):
        return [flag("intent_schema", "model output is not an object")]
    if set(intent) != set(INTENT_FIELDS):
        unexpected = sorted(set(intent) - set(INTENT_FIELDS))
        missing = sorted(set(INTENT_FIELDS) - set(intent))
        return [flag("intent_schema", f"unexpected keys {unexpected}, missing keys {missing}")]
    flags = []
    for field in INTENT_FIELDS:
        claim = intent[field]
        if not isinstance(claim, dict) or set(claim) != {"value", "quote"}:
            flags.append(flag("intent_schema", f"{field} must have exactly value and quote"))
        elif not isinstance(claim["quote"], str):
            flags.append(flag("intent_schema", f"{field} quote must be a string"))
    if flags:
        return flags
    if intent["use_case"]["value"] not in USE_CASES:
        flags.append(flag("intent_schema", "use_case value is not in the allowed list"))
    if intent["urgency"]["value"] not in URGENCIES:
        flags.append(flag("intent_schema", "urgency value is not in the allowed list"))
    competitor = intent["competitor"]["value"]
    if competitor is not None and (not isinstance(competitor, str) or competitor.strip() == ""):
        flags.append(flag("intent_schema", "competitor value must be null or a non-empty string"))
    summary = intent["summary"]["value"]
    if not isinstance(summary, str) or not 0 < len(summary) <= MAX_SUMMARY_LENGTH:
        flags.append(flag("intent_schema", "summary value must be a short non-empty string"))
    return flags


def check_quote_grounding(intent: dict, form_text: str) -> list:
    """Every claim needs a quote, and every quote must appear verbatim in the form text."""
    flags = []
    text = normalise_whitespace(form_text)
    for field in INTENT_FIELDS:
        value = intent[field]["value"]
        quote = normalise_whitespace(intent[field]["quote"])
        makes_claim = value not in (None, "unknown")
        if makes_claim and quote == "":
            flags.append(flag("quote_grounding", f"{field} has a value but no quote"))
        elif quote != "" and quote not in text:
            flags.append(flag("quote_grounding", f"{field} quote is not in the form text"))
    return flags


def check_competitor_grounding(intent: dict, form_text: str) -> list:
    competitor = intent["competitor"]["value"]
    if competitor is None:
        return []
    pattern = rf"\b{re.escape(competitor)}\b"
    if re.search(pattern, form_text, re.IGNORECASE):
        return []
    return [flag("competitor_grounding", "competitor name is not a whole word in the form text")]


def check_intent(intent, form_text: str) -> list:
    """All model-output checks. Grounding is only checked once the shape is right."""
    flags = check_intent_schema(intent)
    if flags:
        return flags
    return check_quote_grounding(intent, form_text) + check_competitor_grounding(intent, form_text)


def check_rules_version(plan: dict, rules: dict) -> list:
    if plan["rules_version"] != rules["rules_version"]:
        return [flag("rules_version", "plan was made with a different rules_version")]
    return []


def check_score_sum(plan: dict) -> list:
    total = sum(line["points"] for line in plan["contributions"])
    if total != plan["score"]:
        return [flag("score_sum", f"contributions sum to {total} but score is {plan['score']}")]
    return []


def check_grade(plan: dict, rules: dict) -> list:
    if plan["disqualifier"] is not None:
        expected = DISQUALIFIED
    else:
        expected = grade_for_score(plan["score"], rules)
    if plan["grade"] != expected:
        return [flag("grade_threshold",
                     f"grade {plan['grade']} does not match rules grade {expected}")]
    return []


def check_actions(plan: dict, rules: dict) -> list:
    policy = rules["grade_policies"].get(plan["grade"])
    if policy is None:
        return [flag("action_allowed", f"grade {plan['grade']} has no policy")]
    if not plan["actions"]:
        return [flag("action_allowed", "no action planned")]
    not_allowed = [action for action in plan["actions"] if action not in policy["actions"]]
    if not_allowed:
        return [flag("action_allowed",
                     f"{not_allowed} not allowed for grade {plan['grade']}")]
    return []


def check_owner(plan: dict, rules: dict) -> list:
    policy = rules["grade_policies"].get(plan["grade"], {})
    owner_id = plan["owner_id"]
    if owner_id is None:
        if policy.get("owner_role") is not None:
            return [flag("owner_in_roster", "grade requires an owner but none is assigned")]
        return []
    if owner_id not in roster_ids(rules):
        return [flag("owner_in_roster", f"owner {owner_id} is not in the roster")]
    return []


def check_required_properties(plan: dict, rules: dict) -> list:
    if not plan["actions"]:
        return []
    required = rules["crm"]["required_properties"].get(plan["actions"][0], [])
    missing = [name for name in required if is_empty(plan["properties"].get(name))]
    if missing:
        return [flag("required_properties", f"missing {missing}")]
    return []


def check_write_scope(plan: dict, rules: dict) -> list:
    """A status-only action may write the status and nothing else."""
    if plan["actions"] != ["status_only"]:
        return []
    extra = sorted(set(plan["properties"]) - set(rules["crm"]["status_only_properties"]))
    if extra:
        return [flag("write_scope", f"status_only may not write {extra}")]
    return []


def check_protected_overwrite(plan: dict, rules: dict) -> list:
    """A non-empty CRM value may only be replaced when the rules mark the field overwritable."""
    overwritable = rules["crm"]["overwritable_properties"]
    blocked = []
    for name, new_value in plan["properties"].items():
        old_value = plan["existing_properties"].get(name)
        if is_empty(old_value) or new_value == old_value or name in overwritable:
            continue
        blocked.append(name)
    if blocked:
        return [flag("protected_overwrite", f"would overwrite CRM values for {sorted(blocked)}")]
    return []


def check_enriched_values(plan: dict, rules: dict) -> list:
    """Re-run each field's validation on enriched values that are about to be written."""
    company_domain = plan["properties"].get("company_domain", "")
    invalid = []
    for field in plan["enriched_fields"]:
        if field not in plan["properties"]:
            continue
        if not field_value_is_valid(field, plan["properties"][field], company_domain, rules):
            invalid.append(field)
    if invalid:
        return [flag("enrichment_validation", f"enriched values fail validation: {invalid}")]
    return []


def validate_plan(plan: dict, rules: dict) -> list:
    """Run every check. Any flag is a hard failure: the lead goes to review, not to the CRM."""
    flags = []
    if plan["model_error"] is not None:
        flags.append(flag("model_call", plan["model_error"]))
    if plan["intent"] is not None:
        flags += check_intent(plan["intent"], plan["form_text"])
    flags += check_rules_version(plan, rules)
    flags += check_score_sum(plan)
    flags += check_grade(plan, rules)
    flags += check_actions(plan, rules)
    flags += check_owner(plan, rules)
    flags += check_required_properties(plan, rules)
    flags += check_write_scope(plan, rules)
    flags += check_protected_overwrite(plan, rules)
    flags += check_enriched_values(plan, rules)
    return flags
