"""Everything that decides. Pure functions over the rules file; no model, no I/O after load."""

import json

DISQUALIFIED = "DISQUALIFIED"


def load_rules(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def is_empty(value) -> bool:
    return value is None or value == "" or value == []


def criterion_met(criterion: dict, facts: dict) -> bool:
    """Evaluate one rule line such as {"field": "industry", "op": "in", "value": [...]}."""
    value = facts.get(criterion["field"])
    op = criterion["op"]
    if op == "is_empty":
        return is_empty(value)
    if op == "not_empty":
        return not is_empty(value)
    if is_empty(value):
        return False
    if op == "is_true":
        return value is True
    if op == "equals":
        return value == criterion["value"]
    if op == "in":
        return value in criterion["value"]
    if op == "between":
        low, high = criterion["value"]
        return low <= value <= high
    if op == "contains_any":
        return any(item in criterion["value"] for item in value)
    raise ValueError(f"unknown rule operator: {op}")


def find_disqualifier(facts: dict, rules: dict) -> str | None:
    """Return the id of the first disqualifier that matches, or None."""
    for criterion in rules["disqualifiers"]:
        if criterion_met(criterion, facts):
            return criterion["id"]
    return None


def score_lead(facts: dict, rules: dict) -> dict:
    """Add up the weights of every criterion the facts meet. Returns score and line items."""
    contributions = []
    for group in ("fit", "intent"):
        for criterion in rules[f"{group}_criteria"]:
            met = criterion_met(criterion, facts)
            contributions.append({
                "id": criterion["id"],
                "group": group,
                "met": met,
                "points": criterion["weight"] if met else 0,
            })
    score = sum(line["points"] for line in contributions)
    return {"score": score, "contributions": contributions}


def grade_for_score(score: int, rules: dict) -> str:
    """Highest grade whose minimum score is reached."""
    thresholds = rules["grade_thresholds"]
    for grade in sorted(thresholds, key=thresholds.get, reverse=True):
        if score >= thresholds[grade]:
            return grade
    raise ValueError(f"score {score} is below every grade threshold")


def roster_ids(rules: dict) -> set:
    return {person["owner_id"] for person in rules["roster"]}


def territory_for_country(country: str | None, rules: dict) -> str:
    for territory, countries in rules["territories"].items():
        if country in countries:
            return territory
    return rules["default_territory"]


def assign_owner(grade: str, facts: dict, existing_owner_id: str | None, rules: dict) -> dict:
    """Pick the owner for a grade. Returns {"owner_id": ..., "reason": ...}."""
    role = rules["grade_policies"][grade]["owner_role"]
    if role is None:
        return {"owner_id": None, "reason": "grade has no owner"}
    if existing_owner_id in roster_ids(rules):
        return {"owner_id": existing_owner_id, "reason": "existing CRM owner kept"}
    territory = territory_for_country(facts.get("hq_country"), rules)
    for person in rules["roster"]:
        if person["role"] == role and person["territory"] == territory:
            return {"owner_id": person["owner_id"], "reason": f"{role} for {territory}"}
    raise ValueError(f"no {role} in roster for territory {territory}")
