import unittest

from agents.retrieval import RetrievalAgent, fix_text, is_misaligned

AGENT = RetrievalAgent()


class RetrievalTest(unittest.TestCase):
    def test_topic_routing(self):
        self.assertEqual(AGENT.detect_content_type("Top 10 Events of Goa"), "events")
        self.assertEqual(AGENT.detect_content_type("Best Beach Resorts in Goa"), "stay")
        self.assertEqual(AGENT.detect_content_type("Best Seafood Restaurants in Goa"), "food")
        self.assertEqual(AGENT.detect_content_type("Top 10 Water Sports in Goa"), "activities")
        self.assertEqual(AGENT.detect_content_type("Best Waterfalls of Goa"), "places")
        self.assertEqual(AGENT.detect_subject("Best Waterfalls of Goa"), "waterfall")

    def test_waterfall_search_is_clean(self):
        res = AGENT.search("Best Waterfalls of Goa", limit=8)
        self.assertGreaterEqual(len(res.records), 5)
        for name in res.names():
            self.assertRegex(name.lower(), r"fall|cascade")
        self.assertEqual(len(set(res.names())), len(res.names()))
        self.assertEqual(res.names()[0], "Dudhsagar Falls")   # most popular first
        self.assertFalse([c for c in res.records.columns if c.startswith("Unnamed")])

    def test_near_duplicates_removed(self):
        names = AGENT.search("Best Waterfalls of Goa", limit=20).names()
        self.assertFalse("Dudhsagar Waterfall" in names and "Dudhsagar Falls" in names)

    def test_fort_word_boundary(self):
        names = AGENT.search("Historic Forts of Goa", limit=10).names()
        self.assertTrue(any("fort" in n.lower() for n in names))

    def test_misaligned_rows_are_quarantined(self):
        self.assertTrue(is_misaligned("Kesarval Waterfall", "Pali Waterfall is a beautiful seasonal waterfall"))
        self.assertFalse(is_misaligned("Pali Waterfall (Shivling Falls)", "Pali Waterfall, also known as Shivling Falls, is"))
        AGENT.load("places")
        bad = AGENT.quarantine.get("places")
        self.assertIsNotNone(bad)
        self.assertNotIn("Kesarval Waterfall", AGENT.search("Best Waterfalls of Goa", limit=50).names())

    def test_text_repair(self):
        self.assertEqual(fix_text("Goaâs beauty ï¿½\\nGoa Tourism\\nStay."), "Goa's beauty Stay.")

    def test_routes_are_merged_for_places(self):
        res = AGENT.search("Historic Forts of Goa", limit=10)
        self.assertTrue(res.routes)
        self.assertIn("ROUTE:", res.to_context())

    def test_facts_for_item(self):
        res = AGENT.search("Best Waterfalls of Goa", limit=3)
        labels = [l for l, _ in res.facts_for("Dudhsagar Falls")]
        self.assertIn("Best time", labels)


if __name__ == "__main__":
    unittest.main()
