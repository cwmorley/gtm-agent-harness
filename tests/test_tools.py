import unittest

from harness.tools import (
    STEP_TOOLS,
    Tool,
    ToolInputError,
    ToolPermissionError,
    ToolRegistry,
    UnregisteredToolError,
)
from tests.helpers import Harness

UPSERT_ARGUMENTS = {"email": "a@b.example", "properties": {"email": "a@b.example"},
                    "idempotency_key": "k1"}


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.registry = self.harness.registry

    def test_every_tool_declares_read_or_write_and_a_schema(self):
        expected = {"crm_search_contact": "read", "enrich_company": "read",
                    "crm_upsert": "write", "crm_create_task": "write",
                    "review_enqueue": "write"}
        self.assertEqual({name: tool.access for name, tool in self.registry.tools.items()},
                         expected)
        for tool in self.registry.tools.values():
            self.assertTrue(tool.input_schema)

    def test_unregistered_tool_raises(self):
        with self.assertRaises(UnregisteredToolError):
            self.registry.call("crm_delete_contact", {}, step="write")

    def test_write_tool_from_a_non_write_step_raises_and_writes_nothing(self):
        for step in ("dedupe", "qualify", "enrich", "interpret", "score", "decide", "validate"):
            with self.assertRaises(ToolPermissionError):
                self.registry.call("crm_upsert", UPSERT_ARGUMENTS, step=step)
        self.assertEqual(self.harness.crm.write_count, 0)
        self.assertEqual(self.registry.call_log, [])

    def test_only_write_and_review_steps_are_allowed_any_write_tool(self):
        for step, names in STEP_TOOLS.items():
            has_write = any(self.registry.tools[name].access == "write" for name in names)
            self.assertEqual(has_write, step in ("write", "review"), step)

    def test_review_step_cannot_touch_the_crm(self):
        with self.assertRaises(ToolPermissionError):
            self.registry.call("crm_upsert", UPSERT_ARGUMENTS, step="review")

    def test_arguments_must_match_the_schema(self):
        with self.assertRaises(ToolInputError):
            self.registry.call("crm_search_contact", {"email": "a@b.example"}, step="dedupe")
        with self.assertRaises(ToolInputError):
            self.registry.call("crm_search_contact",
                               {"email": 7, "company_domain": "b.example"}, step="dedupe")

    def test_allowed_call_runs_and_is_logged_with_access_and_step(self):
        self.registry.call("crm_upsert", UPSERT_ARGUMENTS, step="write")
        self.assertEqual(self.registry.call_log,
                         [{"tool": "crm_upsert", "access": "write", "step": "write"}])
        self.assertIn("a@b.example", self.harness.crm.contacts)

    def test_register_rejects_an_undeclared_access_level(self):
        with self.assertRaises(ValueError):
            ToolRegistry().register(Tool("x", "admin", {}, print))


class FakeCrmWriteTests(unittest.TestCase):
    def test_upsert_with_the_same_idempotency_key_writes_once(self):
        crm = Harness().crm
        first = crm.upsert("a@b.example", {"email": "a@b.example", "lead_status": "new"}, "k1")
        second = crm.upsert("a@b.example", {"email": "a@b.example", "lead_status": "x"}, "k1")
        self.assertEqual((first["status"], second["status"]), ("created", "replayed"))
        self.assertEqual(crm.contacts["a@b.example"]["lead_status"], "new")
        self.assertEqual(crm.write_count, 1)

    def test_review_enqueue_with_the_same_key_queues_once(self):
        store = Harness().review_store
        store.enqueue("L-1", [], "k1")
        self.assertEqual(store.enqueue("L-1", [], "k1"), {"status": "replayed"})
        self.assertEqual(len(store.records), 1)
        self.assertEqual(store.records[0]["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
