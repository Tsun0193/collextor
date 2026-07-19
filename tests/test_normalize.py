import unittest

from scripts.normalize import canonicalize_article, deduplicate, normalize_title, normalize_url, parse_date, title_similarity


SOURCE = {"id": "simon", "name": "Simon", "category": "engineering", "priority": "critical", "page_url": "https://example.com"}


class NormalizeTests(unittest.TestCase):
    def test_url_normalization_removes_tracking(self):
        url = normalize_url("HTTPS://Example.com/post/?utm_source=x&ref=hn&a=1#frag")
        self.assertEqual(url, "https://example.com/post?a=1")

    def test_invalid_non_http_url_removed(self):
        self.assertEqual(normalize_url("javascript:alert(1)"), "")

    def test_title_normalization(self):
        self.assertEqual(normalize_title("  Launch: New AI Model! "), "launch new ai model")

    def test_malformed_date_returns_none(self):
        self.assertIsNone(parse_date("not a date at all"))

    def test_missing_fields_are_rejected(self):
        self.assertIsNone(canonicalize_article({"title": "No URL"}, SOURCE, "2026-01-01T00:00:00Z"))

    def test_malicious_html_stripped_from_description(self):
        article = canonicalize_article({"title": "Clean title", "url": "https://example.com/a", "description": "<img src=x onerror=alert(1)>Hello<script>x</script>"}, SOURCE, "2026-01-01T00:00:00Z")
        self.assertNotIn("<", article["description"])
        self.assertIn("Hello", article["description"])

    def test_deduplication_by_url_and_title_similarity(self):
        fetched = "2026-01-01T00:00:00Z"
        a = canonicalize_article({"title": "OpenAI releases a new multimodal model", "url": "https://example.com/a?utm_medium=x"}, SOURCE, fetched)
        b = canonicalize_article({"title": "OpenAI releases new multimodal model", "url": "https://example.com/a?utm_campaign=y"}, SOURCE, fetched)
        items, removed = deduplicate([a, b])
        self.assertEqual(len(items), 1)
        self.assertEqual(removed, 1)

    def test_title_similarity_for_event_clustering(self):
        self.assertGreater(title_similarity("Google launches a new AI model for coding", "Google releases new AI coding model"), 0.55)


if __name__ == "__main__":
    unittest.main()
