import unittest


class AiToolSchemaTests(unittest.TestCase):
    def test_url_read_schema_exposes_optional_reason(self):
        from app.ai.tools import URL_READ_TOOL

        parameters = URL_READ_TOOL["function"]["parameters"]
        properties = parameters["properties"]

        self.assertIn("url", properties)
        self.assertIn("reason", properties)
        self.assertEqual(properties["full_page"]["type"], "boolean")
        self.assertEqual(parameters["required"], ["url"])
        self.assertFalse(parameters["additionalProperties"])

    def test_url_read_schema_prioritizes_high_value_search_results(self):
        from app.ai.tools import URL_READ_TOOL

        description = URL_READ_TOOL["function"]["description"]

        self.assertIn("official sources", description)
        self.assertIn("primary announcements", description)
        self.assertIn("highly relevant results", description)
        self.assertIn("videos", description)
        self.assertIn("forums", description)
        self.assertIn("low-relevance results", description)
        self.assertIn("Deprioritize", description)


if __name__ == "__main__":
    unittest.main()
