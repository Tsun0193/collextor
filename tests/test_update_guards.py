import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import scripts.update_feeds as updater


class UpdateGuardTests(unittest.TestCase):
    def test_all_fetch_failure_preserves_previous_data(self):
        with TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            (data / "history.json").write_text('{"articles":[{"id":"a","title":"Old","url":"https://example.com","source_name":"S","published_at":"2026-01-01T00:00:00Z"}]}', encoding="utf-8")
            old_data = updater.DATA
            old_fetch = updater.fetch_source
            try:
                updater.DATA = data
                updater.fetch_source = lambda source, fetcher: (_ for _ in ()).throw(RuntimeError("offline"))
                result = updater.run()
            finally:
                updater.DATA = old_data
                updater.fetch_source = old_fetch
            self.assertFalse(result["ok"])
            self.assertIn("Old", (data / "history.json").read_text(encoding="utf-8"))
            self.assertTrue((data / "source-status.json").exists())


if __name__ == "__main__":
    unittest.main()
