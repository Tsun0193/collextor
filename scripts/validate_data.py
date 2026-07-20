from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.normalize import is_valid_http_url, strip_html

ROOT = Path(__file__).resolve().parents[1]
SECRET_LIKE_PATTERNS = (
    "AKIA",
    "ASIA",
    "X-Amz-Credential=",
    "X-Amz-Security-Token=",
    "X-Amz-Signature=",
)


def reject_secret_like_value(value: str | None, field: str) -> None:
    if not value:
        return
    if any(pattern in value for pattern in SECRET_LIKE_PATTERNS):
        raise ValueError(f"secret-like value in {field}")


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
        reject_secret_like_value(item.get("url"), "article url")
        reject_secret_like_value(item.get("canonical_url"), "article canonical_url")
        if item.get("image_url") and not is_valid_http_url(item.get("image_url")):
            item["image_url"] = ""
        reject_secret_like_value(item.get("image_url"), "article image_url")
        if item.get("description") != strip_html(item.get("description")):
            raise ValueError("description contains HTML")


def validate_stocks(data: dict[str, Any]) -> None:
    if not isinstance(data.get("symbols", []), list):
        raise ValueError("stock symbols must be a list")
    for item in data.get("symbols", []):
        for field in ("symbol", "name", "price", "url"):
            if item.get(field) in (None, ""):
                raise ValueError(f"missing stock {field}")
        if not is_valid_http_url(item.get("url")):
            raise ValueError(f"invalid stock url {item.get('url')}")
        reject_secret_like_value(item.get("url"), "stock url")
    analysis = data.get("analysis", {})
    if analysis:
        if analysis.get("related_articles") and not isinstance(analysis["related_articles"], list):
            raise ValueError("stock related_articles must be a list")
        for item in analysis.get("related_articles", []):
            if not is_valid_http_url(item.get("url")):
                raise ValueError(f"invalid related market article url {item.get('url')}")
            reject_secret_like_value(item.get("url"), "related market article url")


def validate_media(data: dict[str, Any]) -> None:
    if not isinstance(data.get("items", []), list):
        raise ValueError("media items must be a list")
    seen = set()
    for item in data.get("items", []):
        for field in ("id", "title", "url", "source_name", "published_at"):
            if not item.get(field):
                raise ValueError(f"missing media {field}")
        if item["id"] in seen:
            raise ValueError(f"duplicate media id {item['id']}")
        seen.add(item["id"])
        if not is_valid_http_url(item.get("url")):
            raise ValueError(f"invalid media url {item.get('url')}")
        reject_secret_like_value(item.get("url"), "media url")
        if item.get("image_url") and not is_valid_http_url(item.get("image_url")):
            item["image_url"] = ""
        reject_secret_like_value(item.get("image_url"), "media image_url")
        if item.get("description") != strip_html(item.get("description")):
            raise ValueError("media description contains HTML")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", default=["data/latest.json", "data/history.json", "data/source-status.json", "data/stocks.json", "data/media.json"])
    args = parser.parse_args()
    for name in args.files:
        path = ROOT / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "articles" in data:
            validate_dataset(data)
        if name.endswith("stocks.json"):
            validate_stocks(data)
        if name.endswith("media.json"):
            validate_media(data)
    print("JSON validation passed")


if __name__ == "__main__":
    main()
