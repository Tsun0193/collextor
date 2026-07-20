import unittest

from scripts.update_feeds import build_market_brief


class MarketBriefTests(unittest.TestCase):
    def test_builds_rule_based_market_brief(self):
        stocks = {
            "symbols": [
                {"symbol": "NVDA", "category": "chips", "change_percent": -3.0},
                {"symbol": "AMD", "category": "chips", "change_percent": -5.0},
                {"symbol": "MSFT", "category": "hyperscaler", "change_percent": 1.0},
            ]
        }
        articles = [{"section": "business_policy", "title": "GPU financing story", "url": "https://example.com/a", "source_name": "Example"}]
        brief = build_market_brief(stocks, articles)
        self.assertIn("headline", brief)
        self.assertGreaterEqual(len(brief["bullets"]), 2)
        self.assertEqual(brief["related_articles"][0]["title"], "GPU financing story")


if __name__ == "__main__":
    unittest.main()
