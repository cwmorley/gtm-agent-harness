import unittest

from harness import crm_hubspot

PROPERTIES = {
    "email": "jordan.avery@brightharbor.example",
    "first_name": "Jordan",
    "last_name": "Avery",
    "company": "Brightharbor Software (fictional)",
    "company_domain": "brightharbor.example",
    "owner_id": "9001",
    "lead_status": "new",
    "lead_grade": "A",
    "lead_score": 95,
    "tech_stack": ["hubspot", "salesforce"],
}


class HubspotRequestTests(unittest.TestCase):
    def setUp(self):
        self.request = crm_hubspot.build_contact_upsert_request(PROPERTIES)

    def test_method_and_path(self):
        self.assertEqual(set(self.request), {"method", "path", "body"})
        self.assertEqual(self.request["method"], "POST")
        self.assertEqual(self.request["path"], "/crm/v3/objects/contacts/batch/upsert")

    def test_body_is_one_input_keyed_by_email(self):
        inputs = self.request["body"]["inputs"]
        self.assertEqual(len(inputs), 1)
        self.assertEqual(set(inputs[0]), {"id", "idProperty", "properties"})
        self.assertEqual(inputs[0]["id"], "jordan.avery@brightharbor.example")
        self.assertEqual(inputs[0]["idProperty"], "email")

    def test_properties_are_renamed_and_include_the_owner(self):
        self.assertEqual(self.request["body"]["inputs"][0]["properties"], {
            "firstname": "Jordan",
            "lastname": "Avery",
            "company": "Brightharbor Software (fictional)",
            "website": "brightharbor.example",
            "hubspot_owner_id": "9001",
            "hs_lead_status": "new",
            "lead_grade": "A",
            "lead_score": "95",
            "lead_tech_stack": "hubspot;salesforce",
        })

    def test_unknown_property_raises_instead_of_being_sent_unmapped(self):
        with self.assertRaises(KeyError):
            crm_hubspot.build_contact_upsert_request({"email": "a@b.example", "ssn": "x"})

    def test_live_send_is_not_implemented(self):
        with self.assertRaises(crm_hubspot.LiveWriteNotImplementedError) as raised:
            crm_hubspot.send_live()
        self.assertIn("not implemented in this demo", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
