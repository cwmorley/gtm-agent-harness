"""Builds (never sends) the HubSpot CRM v3 request for a contact upsert, as plain dicts.

Verified 2026-11-10 by fetching
https://developers.hubspot.com/docs/api-reference/crm-contacts-v3/guide and
https://developers.hubspot.com/docs/api-reference/crm-contacts-v3/batch/post-crm-v3-objects-contacts-batch-upsert
  - POST /crm/v3/objects/contacts/batch/upsert
  - body {"inputs": [{"id": <email>, "idProperty": "email", "properties": {...}}]}
  - at most 100 records per batch call
  - the guide warns: "Partial upserts are not supported when using email as the idProperty
    for contacts"; a production build would upsert on a custom unique property instead
  - firstname, lastname, company, website appear as contact property names in the guide

NOT verified by a fetched page (marked UNVERIFIED below):
  - hubspot_owner_id as the owner property. https://developers.hubspot.com/docs/guides/api/crm/owners
    says an owner ID is used "when assigning an owner to a record" but does not name the property.
  - hs_lead_status as the lead status property and its allowed values.
  - every other property name below is a custom property this demo assumes would be created
    in the portal first.
  - sending numbers as strings and multi-value fields joined with ";".
  - the authentication header.
"""

UPSERT_PATH = "/crm/v3/objects/contacts/batch/upsert"

# harness property name -> HubSpot property name
PROPERTY_NAMES = {
    "first_name": "firstname",
    "last_name": "lastname",
    "company": "company",
    "company_domain": "website",
    "owner_id": "hubspot_owner_id",        # UNVERIFIED
    "lead_status": "hs_lead_status",       # UNVERIFIED
    "work_email": "work_email",            # assumed custom property, as are all below
    "industry": "lead_industry",
    "employee_count": "lead_employee_count",
    "hq_country": "lead_hq_country",
    "tech_stack": "lead_tech_stack",
    "lead_grade": "lead_grade",
    "lead_score": "lead_score",
    "intent_use_case": "intent_use_case",
    "intent_urgency": "intent_urgency",
    "intent_summary": "intent_summary",
    "competitor_mentioned": "competitor_mentioned",
    "rules_version": "routing_rules_version",
    "sla_minutes": "routing_sla_minutes",
}


class LiveWriteNotImplementedError(Exception):
    pass


def to_hubspot_value(value) -> str:
    if isinstance(value, list):
        return ";".join(value)
    return str(value)


def build_contact_upsert_request(properties: dict) -> dict:
    """Return {"method", "path", "body"} for one contact. The email is the upsert id."""
    hubspot_properties = {}
    for name, value in properties.items():
        if name == "email":
            continue
        hubspot_properties[PROPERTY_NAMES[name]] = to_hubspot_value(value)
    return {
        "method": "POST",
        "path": UPSERT_PATH,
        "body": {"inputs": [{
            "id": properties["email"],
            "idProperty": "email",
            "properties": hubspot_properties,
        }]},
    }


def send_live() -> None:
    """The --live path stops here on purpose so nothing can write by accident."""
    raise LiveWriteNotImplementedError(
        "live HubSpot writes are not implemented in this demo; nothing was sent")
