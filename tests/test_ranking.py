import unittest
from datetime import datetime, timezone

import yaml

from scripts.ranking import score_article, weekly_eligible


def cfg():
    with open("config/ranking.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def article(title, description=""):
    return {
        "title": title,
        "description": description,
        "authors": [],
        "published_at": "2026-07-19T00:00:00Z",
        "source_category": "engineering",
        "research_track": "",
        "is_long_read": False,
    }


class RankingTests(unittest.TestCase):
    def test_source_weight_scoring(self):
        scored = score_article(article("A modest engineering note"), {"priority": "critical", "category": "engineering"}, cfg(), now=datetime(2026, 7, 19, 3, tzinfo=timezone.utc))
        self.assertGreaterEqual(scored["score"], 13)

    def test_research_keyword_boost_and_weekly_retention(self):
        item = article("MRI foundation model for Alzheimer's disease diagnosis", "multimodal medical imaging")
        item["research_track"] = "medical_neuroimaging"
        scored = score_article(item, {"priority": "medium", "category": "research"}, cfg(), now=datetime(2026, 7, 19, 3, tzinfo=timezone.utc))
        self.assertGreaterEqual(scored["score"], 13)
        self.assertTrue(weekly_eligible(scored, cfg()))

    def test_negative_signal_penalty(self):
        scored = score_article(article("Sponsored webinar: top 50 AI tools"), {"priority": "high", "category": "industry"}, cfg(), now=datetime(2026, 7, 19, 3, tzinfo=timezone.utc))
        self.assertTrue(any("negative signal" in r for r in scored["score_reasons"]))


if __name__ == "__main__":
    unittest.main()
