from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.normalize import is_valid_http_url, strip_html

ROOT = Path(__file__).resolve().parents[1]


def validate_dataset(data: dict[str, Any]) -> None:
    articles = data.get("articles", [])
    if not isinstance(articles, list):
        raise ValueError("articles must be a list")
    seen = set()
    for item in articles:
        for field in ("id", "title", "url", "source_name", "published_at"):
            if not item.get(field):
                raise ValueError(f"missing {field}")
        if item["id"] in seen:
            raise ValueError(f"duplicate id {item['id']}")
        seen.add(item["id"])
        if not is_valid_http_url(item.get("url")):
            raise ValueError(f"invalid article url {item.get('url')}")
        if item.get("image_url") and not is_valid_http_url(item.get("image_url")):
            item["image_url"] = ""
        if item.get("description") != strip_html(item.get("description")):
            raise ValueError("description contains HTML")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", default=["data/latest.json", "data/history.json", "data/archive-index.json", "data/source-status.json"])
    args = parser.parse_args()
    for name in args.files:
        path = ROOT / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "articles" in data:
            validate_dataset(data)
    for path in (ROOT / "data" / "weekly").glob("*.json"):
        validate_dataset(json.loads(path.read_text(encoding="utf-8")))
    print("JSON validation passed")


if __name__ == "__main__":
    main()
