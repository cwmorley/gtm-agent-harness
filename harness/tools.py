"""The tool registry: the only way a pipeline step can touch the CRM, a provider or the queue.

Each tool declares a name, an input schema and whether it reads or writes. Each step has an
explicit allow-list. Anything else raises.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from harness.normalise import normalise_domain, normalise_email

SCHEMA_TYPES = {"string": str, "object": dict, "array": list, "integer": int}

STEP_TOOLS = {
    "dedupe": ("crm_search_contact",),
    "enrich": ("enrich_company",),
    "write": ("crm_search_contact", "crm_upsert", "crm_create_task"),
    "review": ("review_enqueue",),
}


class UnregisteredToolError(Exception):
    pass


class ToolPermissionError(Exception):
    pass


class ToolInputError(Exception):
    pass


class FakeCRM:
    """In-memory stand-in for a CRM. Contacts are keyed by normalised email."""

    def __init__(self, contacts: list):
        self.contacts = {}
        for contact in contacts:
            self.contacts[normalise_email(contact["email"])] = dict(contact)
        self.tasks = []
        self.applied_keys = set()
        self.write_count = 0

    def search_contact(self, email: str, company_domain: str) -> dict:
        """Dedupe lookup: exact person by email first, then anyone at the same company."""
        key = normalise_email(email)
        if key in self.contacts:
            return {"match": "email", "properties": dict(self.contacts[key])}
        domain = normalise_domain(company_domain)
        if domain:
            for contact in self.contacts.values():
                if contact.get("company_domain") == domain:
                    return {"match": "company_domain", "properties": dict(contact)}
        return {"match": "none", "properties": {}}

    def upsert(self, email: str, properties: dict, idempotency_key: str) -> dict:
        if idempotency_key in self.applied_keys:
            return {"status": "replayed"}
        key = normalise_email(email)
        status = "updated" if key in self.contacts else "created"
        record = self.contacts.get(key, {})
        record.update(properties)
        self.contacts[key] = record
        self.applied_keys.add(idempotency_key)
        self.write_count += 1
        return {"status": status}

    def create_task(self, email: str, owner_id: str, subject: str, due_minutes: int) -> dict:
        self.tasks.append({"email": normalise_email(email), "owner_id": owner_id,
                           "subject": subject, "due_minutes": due_minutes})
        self.write_count += 1
        return {"status": "created"}


class ReviewStore:
    """Where a lead goes when the validator or a write guard stops it."""

    def __init__(self):
        self.records = []
        self.applied_keys = set()

    def enqueue(self, lead_id: str, flags: list, idempotency_key: str) -> dict:
        if idempotency_key in self.applied_keys:
            return {"status": "replayed"}
        self.records.append({"lead_id": lead_id, "status": "needs_review", "flags": flags})
        self.applied_keys.add(idempotency_key)
        return {"status": "queued"}


def load_crm(path: str) -> FakeCRM:
    with open(path, encoding="utf-8") as handle:
        return FakeCRM(json.load(handle)["contacts"])


@dataclass
class Tool:
    name: str
    access: str  # "read" or "write"
    input_schema: dict  # argument name -> "string" | "object" | "array" | "integer"
    handler: Callable  # the function that does the work


class ToolRegistry:
    def __init__(self):
        self.tools = {}
        self.call_log = []

    def register(self, tool: Tool) -> None:
        if tool.access not in ("read", "write"):
            raise ValueError(f"tool {tool.name} must declare access as read or write")
        self.tools[tool.name] = tool

    def call(self, name: str, arguments: dict, step: str):
        """Run one tool on behalf of one pipeline step, after checking it is allowed."""
        if name not in self.tools:
            raise UnregisteredToolError(f"tool {name} is not registered")
        tool = self.tools[name]
        if name not in STEP_TOOLS.get(step, ()):
            raise ToolPermissionError(
                f"step {step} may not call {tool.access} tool {name}")
        check_arguments(tool, arguments)
        result = tool.handler(**arguments)
        self.call_log.append({"tool": name, "access": tool.access, "step": step})
        return result


def check_arguments(tool: Tool, arguments: dict) -> None:
    if set(arguments) != set(tool.input_schema):
        raise ToolInputError(
            f"{tool.name} expects arguments {sorted(tool.input_schema)}, got {sorted(arguments)}")
    for name, type_name in tool.input_schema.items():
        if not isinstance(arguments[name], SCHEMA_TYPES[type_name]):
            raise ToolInputError(f"{tool.name} argument {name} must be a {type_name}")


def build_registry(crm: FakeCRM, enrichment_service, review_store: ReviewStore) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool(
        name="crm_search_contact", access="read",
        input_schema={"email": "string", "company_domain": "string"},
        handler=crm.search_contact))
    registry.register(Tool(
        name="enrich_company", access="read",
        input_schema={"company_domain": "string", "full_name": "string", "fields": "array"},
        handler=enrichment_service.enrich))
    registry.register(Tool(
        name="crm_upsert", access="write",
        input_schema={"email": "string", "properties": "object", "idempotency_key": "string"},
        handler=crm.upsert))
    registry.register(Tool(
        name="crm_create_task", access="write",
        input_schema={"email": "string", "owner_id": "string", "subject": "string",
                      "due_minutes": "integer"},
        handler=crm.create_task))
    registry.register(Tool(
        name="review_enqueue", access="write",
        input_schema={"lead_id": "string", "flags": "array", "idempotency_key": "string"},
        handler=review_store.enqueue))
    return registry
