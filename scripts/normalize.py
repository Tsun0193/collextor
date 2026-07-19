from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "utm_id",
    "ref",
    "source",
    "fbclid",
    "gclid",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (list, tuple)) and len(value) >= 6:
        dt = datetime(*value[:6], tzinfo=timezone.utc)
    else:
        text = str(value).strip()
        try:
            dt = parsedate_to_datetime(text)
        except Exception:
            try:
                dt = date_parser.parse(text)
            except Exception:
                return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def normalize_url(url: str | None, base_url: str | None = None) -> str:
    if not url:
        return ""
    url = html.unescape(url.strip())
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path or "")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", urlencode(query, doseq=True), ""))


def normalize_title(title: str | None) -> str:
    text = strip_html(title or "")
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"[^\w\s]", "", text)
    return text


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(str(value), "html.parser")
    return html.unescape(soup.get_text(" ", strip=True))


def truncate(value: str | None, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", strip_html(value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].strip() + "..."


def safe_authors(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [strip_html(value)[:100]]
    authors = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = str(item)
        if name:
            authors.append(strip_html(name)[:100])
    return authors[:8]


def article_id(url: str, title: str = "") -> str:
    stable = normalize_url(url) or normalize_title(title)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]


def title_tokens(title: str) -> set[str]:
    return {t for t in normalize_title(title).split() if len(t) > 2}


def title_similarity(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()
    return max(jaccard, seq * 0.8)


def is_valid_http_url(url: str | None) -> bool:
    return bool(normalize_url(url))


def canonicalize_article(raw: dict[str, Any], source: dict[str, Any], fetched_at: str) -> dict[str, Any] | None:
    url = normalize_url(raw.get("url") or raw.get("link"), source.get("page_url"))
    if not url:
        return None
    title = strip_html(raw.get("title"))
    if not title:
        return None
    published_at = parse_date(raw.get("published_at") or raw.get("published") or raw.get("date"))
    if not published_at and raw.get("allow_missing_date"):
        published_at = fetched_at
    if not published_at:
        return None
    image_url = normalize_url(raw.get("image_url"), source.get("page_url"))
    item = {
        "id": raw.get("id") or article_id(url, title),
        "title": title[:240],
        "url": url,
        "canonical_url": url,
        "source_id": source["id"],
        "source_name": source["name"],
        "source_category": source.get("category", ""),
        "section": "",
        "published_at": published_at,
        "fetched_at": fetched_at,
        "description": truncate(raw.get("description") or raw.get("summary") or raw.get("abstract"), 240),
        "image_url": image_url,
        "authors": safe_authors(raw.get("authors")),
        "score": 0,
        "score_reasons": [],
        "is_breaking": False,
        "is_must_read": False,
        "is_long_read": bool(source.get("weekly_only") or source.get("category") == "long-read"),
        "research_track": raw.get("research_track") or source.get("research_track") or "",
        "event_cluster_id": "",
    }
    return item


def deduplicate(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen_urls: dict[str, dict[str, Any]] = {}
    seen_titles: dict[str, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    duplicates = 0
    for item in sorted(items, key=lambda x: (x.get("published_at") or "", x.get("score", 0)), reverse=True):
        url_key = normalize_url(item.get("canonical_url") or item.get("url"))
        title_key = normalize_title(item.get("title"))
        duplicate = seen_urls.get(url_key) or seen_titles.get(title_key)
        if not duplicate:
            for existing in result:
                if title_similarity(item["title"], existing["title"]) >= 0.88:
                    duplicate = existing
                    break
        if duplicate:
            duplicates += 1
            duplicate.setdefault("also_covered_by", [])
            source = item.get("source_name")
            if source and source != duplicate.get("source_name") and source not in duplicate["also_covered_by"]:
                duplicate["also_covered_by"].append(source)
            continue
        seen_urls[url_key] = item
        seen_titles[title_key] = item
        result.append(item)
    return result, duplicates


def cluster_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda x: x.get("score", 0), reverse=True):
        match = None
        for cluster in clusters:
            if title_similarity(item["title"], cluster["title"]) >= 0.72:
                match = cluster
                break
        if match:
            item["event_cluster_id"] = match["event_cluster_id"]
            match.setdefault("cluster_sources", [])
            if item["source_name"] not in match["cluster_sources"]:
                match["cluster_sources"].append(item["source_name"])
        else:
            item["event_cluster_id"] = article_id(item["canonical_url"], item["title"])[:12]
            item["cluster_sources"] = [item["source_name"]]
            clusters.append(item)
    return items
