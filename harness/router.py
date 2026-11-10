"""The router: a fixed, deterministic pipeline for one form fill.

    dedupe -> qualify -> enrich -> interpret -> score -> decide -> validate -> write

The router decides which steps run. Rules decide the grade, owner and action. The model only
runs in "interpret", and its output is checked before the rules may use it.
"""

import json
import time
from datetime import datetime, timezone

from harness import crm_hubspot, rules as rules_module, validator, write_guard
from harness.enrichment import fields_to_enrich
from harness.model import PROMPT_VERSION, ModelError
from harness.normalise import (
    normalise_domain,
    normalise_email,
    normalise_label,
    sha256_hex,
)

STEPS = ("dedupe", "qualify", "enrich", "interpret", "score", "decide", "validate", "write")
COMPANY_FIELDS = ("employee_count", "industry", "hq_country", "tech_stack")


def read_form(payload: dict, rules: dict) -> dict:
    """Normalise the raw form payload into the field names the rest of the harness uses."""
    email = normalise_email(payload["email"])
    email_domain = email.rsplit("@", 1)[1]
    business_email = email_domain not in rules["free_mail_domains"]
    company_domain = normalise_domain(payload.get("website", ""))
    if not company_domain and business_email:
        company_domain = email_domain
    return {
        "email": email,
        "first_name": payload.get("first_name", "").strip(),
        "last_name": payload.get("last_name", "").strip(),
        "company": payload.get("company", "").strip(),
        "company_domain": company_domain,
        "business_email": business_email,
        "work_email": email if business_email else "",
        "employee_count": payload.get("employee_count"),
        "industry": normalise_label(payload.get("industry", "")),
        "hq_country": payload.get("country", "").strip().upper(),
        "tech_stack": [],
        "form_text": payload.get("how_can_we_help", "").strip(),
    }


def merge_known(form: dict, found: dict, rules: dict) -> dict:
    """Combine what the form says with what the CRM already has.

    A non-empty CRM value wins unless the form has a value and the field is overwritable.
    Company facts are reused from a colleague's record; a work email is only reused from the
    person's own record.
    """
    overwritable = rules["crm"]["overwritable_properties"]
    crm = found["properties"]
    known = dict(form)
    reusable = list(COMPANY_FIELDS)
    if found["match"] == "email":
        reusable.append("work_email")
    for field in reusable:
        crm_value = crm.get(field)
        form_has_value = not rules_module.is_empty(form[field])
        if rules_module.is_empty(crm_value):
            continue
        if form_has_value and field in overwritable:
            continue
        known[field] = crm_value
    return known


def build_properties(known: dict, intent, score: int, grade: str, owner_id, rules: dict) -> dict:
    """The CRM properties this lead would write, given its grade's policy."""
    policy = rules["grade_policies"][grade]
    properties = {"email": known["email"], "lead_status": policy["lead_status"]}
    if policy["actions"] == ["status_only"]:
        return properties
    properties.update({
        "first_name": known["first_name"],
        "last_name": known["last_name"],
        "company": known["company"],
        "company_domain": known["company_domain"],
        "work_email": known["work_email"],
        "employee_count": known["employee_count"],
        "industry": known["industry"],
        "hq_country": known["hq_country"],
        "tech_stack": known["tech_stack"],
        "lead_grade": grade,
        "lead_score": score,
        "rules_version": rules["rules_version"],
        "sla_minutes": policy["sla_minutes"],
        "owner_id": owner_id,
    })
    if intent is not None:
        properties.update({
            "intent_use_case": intent["use_case"]["value"],
            "intent_urgency": intent["urgency"]["value"],
            "intent_summary": intent["summary"]["value"],
            "competitor_mentioned": intent["competitor"]["value"],
        })
    return {name: value for name, value in properties.items()
            if not rules_module.is_empty(value)}


def protect_existing(properties: dict, existing: dict, rules: dict) -> dict:
    """Keep the CRM's value for any non-empty field the rules do not mark overwritable."""
    overwritable = rules["crm"]["overwritable_properties"]
    protected = dict(properties)
    for name in properties:
        if not rules_module.is_empty(existing.get(name)) and name not in overwritable:
            protected[name] = existing[name]
    return protected


def enrichment_for_audit(skipped: dict, result) -> dict:
    """Provenance without values: an enriched value can be an email address."""
    fields = {}
    cost = 0
    if result is not None:
        cost = result["cost"]
        for field, outcome in result["fields"].items():
            fields[field] = {
                "provider": outcome["provider"],
                "cost": outcome["cost"],
                "validation_passed": outcome["validation_passed"],
                "attempts": outcome["attempts"],
            }
    return {"skipped": skipped, "fields": fields, "cost": cost}


def intent_for_audit(intent) -> dict:
    """Labels in the clear; free text (summary, quotes) as hashes."""
    return {
        "use_case": intent["use_case"]["value"],
        "urgency": intent["urgency"]["value"],
        "competitor": intent["competitor"]["value"],
        "summary_hash": sha256_hex(intent["summary"]["value"]),
        "quote_hashes": {field: sha256_hex(intent[field]["quote"]) for field in intent},
    }


def note_step(state: dict, step: str, status: str, note: str) -> None:
    state["steps"].append({"step": step, "status": status, "note": note})


def step_dedupe(state: dict, rules: dict, registry) -> None:
    """One CRM read. Remember what we read so the stale-state guard can compare later."""
    form = state["form"]
    state["search_arguments"] = {"email": form["email"],
                                 "company_domain": form["company_domain"]}
    found = registry.call("crm_search_contact", state["search_arguments"], step="dedupe")
    state["found"] = found
    state["own_record_at_dedupe"] = found["properties"] if found["match"] == "email" else {}
    state["known"] = merge_known(form, found, rules)
    note_step(state, "dedupe", "ran", f"match: {found['match']}")


def step_qualify(state: dict, rules: dict) -> None:
    """Rules can reject a lead before any money is spent on it."""
    state["disqualifier"] = rules_module.find_disqualifier(state["known"], rules)
    note_step(state, "qualify", "ran", state["disqualifier"] or "no disqualifier")


def step_enrich(state: dict, rules: dict, registry) -> None:
    """Only fields nobody has yet, and never for a disqualified lead."""
    form, known = state["form"], state["known"]
    state["enrichment_result"] = None
    state["enriched_fields"] = []
    needed, state["skipped_fields"] = fields_to_enrich(known, rules)
    if state["disqualifier"] is not None:
        state["skipped_fields"] = dict.fromkeys(rules["enrichment"]["fields"],
                                                "lead disqualified")
        note_step(state, "enrich", "skipped", "lead disqualified")
        return
    if not needed:
        note_step(state, "enrich", "skipped", "all fields already known")
        return
    result = registry.call(
        "enrich_company",
        {"company_domain": form["company_domain"],
         "full_name": f"{form['first_name']} {form['last_name']}", "fields": needed},
        step="enrich")
    for field, outcome in result["fields"].items():
        if outcome["validation_passed"]:
            known[field] = outcome["value"]
            state["enriched_fields"].append(field)
    state["enrichment_result"] = result
    note_step(state, "enrich", "ran", f"fields: {needed}")
    # Enrichment can reveal a disqualifier, for example the industry.
    state["disqualifier"] = rules_module.find_disqualifier(known, rules)


def step_interpret(state: dict, model) -> None:
    """The only model call. Its output is unusable until the validator clears it."""
    form_text = state["form"]["form_text"]
    state["intent"] = None
    state["model_error"] = None
    state["model_called"] = False
    if state["disqualifier"] is not None:
        note_step(state, "interpret", "skipped", "lead disqualified")
    elif form_text == "":
        note_step(state, "interpret", "skipped", "no free text")
    else:
        try:
            state["intent"] = model.extract_intent(form_text)
        except ModelError as error:
            state["model_error"] = str(error)
        state["model_called"] = True
        note_step(state, "interpret", "ran", model.model_id)
    state["usable_intent"] = None
    if state["intent"] is not None and not validator.check_intent(state["intent"], form_text):
        state["usable_intent"] = state["intent"]


def step_score(state: dict, rules: dict) -> None:
    """Rules add up fit and intent. Intent only counts if it passed its checks."""
    if state["disqualifier"] is not None:
        state["score"], state["contributions"] = 0, []
        state["grade"] = rules_module.DISQUALIFIED
        note_step(state, "score", "skipped", "lead disqualified")
        return
    facts = dict(state["known"])
    if state["usable_intent"] is not None:
        for name in ("use_case", "urgency", "competitor"):
            facts[name] = state["usable_intent"][name]["value"]
    scored = rules_module.score_lead(facts, rules)
    state["score"], state["contributions"] = scored["score"], scored["contributions"]
    state["grade"] = rules_module.grade_for_score(state["score"], rules)
    note_step(state, "score", "ran", f"{state['score']} -> {state['grade']}")


def step_decide(state: dict, rules: dict) -> None:
    """The grade's policy sets the actions; assignment rules set the owner."""
    state["policy"] = rules["grade_policies"][state["grade"]]
    state["actions"] = list(state["policy"]["actions"])
    state["owner"] = rules_module.assign_owner(
        state["grade"], state["known"], state["found"]["properties"].get("owner_id"), rules)
    properties = build_properties(state["known"], state["usable_intent"], state["score"],
                                  state["grade"], state["owner"]["owner_id"], rules)
    state["properties"] = protect_existing(properties, state["own_record_at_dedupe"], rules)
    note_step(state, "decide", "ran", f"actions: {state['actions']}")


def step_validate(state: dict, rules: dict) -> None:
    """Every check, before anything is written."""
    plan = {
        "rules_version": rules["rules_version"],
        "form_text": state["form"]["form_text"],
        "intent": state["intent"],
        "model_error": state["model_error"],
        "disqualifier": state["disqualifier"],
        "score": state["score"],
        "contributions": state["contributions"],
        "grade": state["grade"],
        "actions": state["actions"],
        "owner_id": state["owner"]["owner_id"],
        "properties": state["properties"],
        "existing_properties": state["own_record_at_dedupe"],
        "enriched_fields": state["enriched_fields"],
    }
    state["flags"] = validator.validate_plan(plan, rules)
    note_step(state, "validate", "ran", f"{len(state['flags'])} flag(s)")


def step_write(state: dict, rules: dict, registry) -> None:
    """Write behind two guards (stale state, idempotency), or send the lead to review."""
    state["record_changed"] = []
    state["crm_request"] = None
    if not state["flags"]:
        current = registry.call("crm_search_contact", state["search_arguments"], step="write")
        own_record_now = current["properties"] if current["match"] == "email" else {}
        state["record_changed"] = write_guard.changed_fields(state["own_record_at_dedupe"],
                                                             own_record_now)
    if state["flags"] or state["record_changed"]:
        send_to_review(state, rules, registry)
    else:
        write_to_crm(state, rules, registry)


def send_to_review(state: dict, rules: dict, registry) -> None:
    state["final_action"] = "review"
    state["idempotency_key"] = write_guard.idempotency_key(
        state["input_hash"], rules["rules_version"], "review")
    review_flags = list(state["flags"])
    if state["record_changed"]:
        review_flags.append(validator.flag(
            "record_changed", f"changed since dedupe: {state['record_changed']}"))
    result = registry.call(
        "review_enqueue",
        {"lead_id": state["lead_id"], "flags": review_flags,
         "idempotency_key": state["idempotency_key"]},
        step="review")
    state["replay"] = result["status"] == "replayed"
    note_step(state, "write", "skipped", f"review queue: {result['status']}")


def write_to_crm(state: dict, rules: dict, registry) -> None:
    email = state["form"]["email"]
    state["final_action"] = state["actions"][0]
    state["idempotency_key"] = write_guard.idempotency_key(
        state["input_hash"], rules["rules_version"], state["final_action"])
    result = registry.call(
        "crm_upsert",
        {"email": email, "properties": state["properties"],
         "idempotency_key": state["idempotency_key"]},
        step="write")
    state["replay"] = result["status"] == "replayed"
    if not state["replay"] and "create_task" in state["actions"]:
        registry.call(
            "crm_create_task",
            {"email": email, "owner_id": state["owner"]["owner_id"],
             "subject": f"Call grade {state['grade']} inbound lead",
             "due_minutes": state["policy"]["sla_minutes"]},
            step="write")
    request = crm_hubspot.build_contact_upsert_request(state["properties"])
    state["crm_request"] = {
        "method": request["method"], "path": request["path"],
        "property_names": sorted(request["body"]["inputs"][0]["properties"])}
    note_step(state, "write", "ran", result["status"])


def build_audit_entry(state: dict, rules: dict, model, tools_called: list,
                      duration_ms: float) -> dict:
    """What goes in the log: decisions, provenance and hashes. No form text, no emails."""
    form = state["form"]
    model_info = None
    if state["model_called"]:
        model_info = {"model_id": model.model_id, "prompt_version": PROMPT_VERSION}
    intent = None
    if state["usable_intent"] is not None:
        intent = intent_for_audit(state["usable_intent"])
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lead_id": state["lead_id"],
        "input_hash": state["input_hash"],
        "email_hash": sha256_hex(form["email"]),
        "company_domain_hash": sha256_hex(form["company_domain"]),
        "rules_version": rules["rules_version"],
        "steps": state["steps"],
        "dedupe": state["found"]["match"],
        "tools_called": tools_called,
        "disqualifier": state["disqualifier"],
        "enrichment": enrichment_for_audit(state["skipped_fields"], state["enrichment_result"]),
        "model": model_info,
        "intent": intent,
        "score": state["score"],
        "contributions": state["contributions"],
        "grade": state["grade"],
        "planned_actions": state["actions"],
        "owner_id": state["owner"]["owner_id"],
        "owner_reason": state["owner"]["reason"],
        "sla_minutes": state["policy"]["sla_minutes"],
        "validator": {"passed": not state["flags"], "flags": state["flags"]},
        "write_guards": {"idempotency_key": state["idempotency_key"],
                         "replay": state["replay"],
                         "record_changed": state["record_changed"]},
        "crm_request": state["crm_request"],
        "final_action": "replay_no_write" if state["replay"] else state["final_action"],
        "duration_ms": duration_ms,
    }


def process_lead(payload: dict, rules: dict, registry, model) -> dict:
    """Run one form fill through every step, in order. Returns the audit entry for it."""
    started = time.perf_counter()
    first_call = len(registry.call_log)
    state = {
        "lead_id": payload["lead_id"],
        "input_hash": sha256_hex(json.dumps(payload, sort_keys=True)),
        "form": read_form(payload, rules),
        "steps": [],
    }
    step_dedupe(state, rules, registry)
    step_qualify(state, rules)
    step_enrich(state, rules, registry)
    step_interpret(state, model)
    step_score(state, rules)
    step_decide(state, rules)
    step_validate(state, rules)
    step_write(state, rules, registry)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    return build_audit_entry(state, rules, model, registry.call_log[first_call:], duration_ms)
