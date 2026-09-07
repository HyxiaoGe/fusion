import unittest


class AiToolSchemaTests(unittest.TestCase):
    def test_web_search_schema_exposes_model_result_count(self):
        from app.ai.tools import build_web_search_tool

        tool = build_web_search_tool()
        properties = tool["function"]["parameters"]["properties"]

        self.assertIn("query", properties)
        self.assertEqual(properties["count"]["minimum"], 1)
        self.assertEqual(properties["count"]["maximum"], 20)
        self.assertEqual(properties["count"]["default"], 10)
        self.assertIn("intent", properties)
        self.assertIn("domains", properties)
        self.assertIn("recency_days", properties)
        self.assertEqual(
            properties["intent"]["enum"],
            [
                "quick_fact",
                "freshness",
                "comparison",
                "deep_research",
                "official_source",
            ],
        )
        self.assertEqual(tool["function"]["parameters"]["required"], ["query"])

    def test_web_search_tool_description_avoids_duplicate_queries(self):
        from app.ai.tools import build_web_search_tool

        tool = build_web_search_tool()

        description = tool["function"]["description"]
        query_description = tool["function"]["parameters"]["properties"]["query"]["description"]

        self.assertIn("independent queries in parallel", description)
        self.assertIn("evidence gaps", description)
        self.assertIn("original reporting", description)
        self.assertNotIn("third search is only", description)
        self.assertIn("language of the likely original sources", query_description)
        self.assertNotIn("Use the same language", query_description)

    def test_web_search_tool_description_guides_autonomous_natural_questions(self):
        from app.ai.tools import build_web_search_tool

        tool = build_web_search_tool()
        description = tool["function"]["description"]

        self.assertIn("even if the user does not explicitly say", description)
        self.assertIn("current usage", description)
        self.assertIn("integration", description)
        self.assertIn("interoperability", description)
        self.assertIn("How do I use WeChat A2A interoperability?", description)
        self.assertIn("Casual conversation", description)
        self.assertIn("simple arithmetic", description)

    def test_web_search_tool_description_keeps_stable_product_facts_offline(self):
        from app.ai.tools import build_web_search_tool

        tool = build_web_search_tool()
        description = tool["function"]["description"]

        self.assertIn("stable background", description)
        self.assertIn("historical causes", description)
        self.assertIn("why the iPhone moved from Lightning to USB-C", description)
        self.assertIn("value, risks, and adoption advice", description)

    def test_url_read_schema_exposes_optional_reason(self):
        from app.ai.tools import URL_READ_TOOL

        parameters = URL_READ_TOOL["function"]["parameters"]
        properties = parameters["properties"]

        self.assertIn("url", properties)
        self.assertIn("reason", properties)
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
