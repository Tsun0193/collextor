from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from scripts.normalize import normalize_url, strip_html, truncate

UA = "COLLEXTOR/1.0 (+https://github.com/Tsun0193/collextor; personal static newspaper)"


class Fetcher:
    def __init__(self, timeout=(8, 18), retries=1):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/json, text/html;q=0.8"})
        self.timeout = timeout
        self.retries = retries

    def get(self, url: str) -> requests.Response:
        last = None
        for attempt in range(self.retries + 1):
            try:
                res = self.session.get(url, timeout=self.timeout)
                res.raise_for_status()
                return res
            except Exception as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(1 + attempt)
        raise last


def fetch_source(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    typ = source.get("source_type")
    if typ in {"rss", "atom"}:
        return fetch_feed(source, fetcher)
    if typ == "html":
        return fetch_listing(source, fetcher)
    if source["id"] == "hacker_news":
        return fetch_hacker_news(source, fetcher)
    if source["id"].startswith("arxiv_"):
        return fetch_arxiv(source, fetcher)
    if source["id"] == "europe_pmc":
        return fetch_europe_pmc(source, fetcher)
    return []


def fetch_feed(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    res = fetcher.get(source["feed_url"])
    parsed = feedparser.parse(res.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"Invalid feed: {parsed.bozo_exception}")
    items = []
    for entry in parsed.entries[: source.get("max_items", 20)]:
        image = image_from_entry(entry)
        items.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published_at": entry.get("published") or entry.get("updated"),
            "description": entry.get("summary") or entry.get("description", ""),
            "image_url": image,
            "authors": entry.get("authors") or entry.get("author", ""),
        })
    return keyword_filter(items, source)


def image_from_entry(entry: Any) -> str:
    for key in ("media_thumbnail", "media_content"):
        values = entry.get(key)
        if values:
            return values[0].get("url", "")
    for link in entry.get("links", []):
        if str(link.get("type", "")).startswith("image/"):
            return link.get("href", "")
    summary = entry.get("summary") or ""
    soup = BeautifulSoup(summary, "html.parser")
    img = soup.find("img")
    return img.get("src", "") if img else ""


def fetch_listing(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    res = fetcher.get(source["page_url"])
    soup = BeautifulSoup(res.text, "html.parser")
    items = parse_jsonld_listing(soup, source)
    if not items:
        items = parse_anchor_listing(soup, source)
    return keyword_filter(items[: source.get("max_items", 10)], source)


def parse_jsonld_listing(soup: BeautifulSoup, source: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        for node in stack:
            candidates = []
            if isinstance(node, dict):
                if isinstance(node.get("@graph"), list):
                    candidates.extend(node["@graph"])
                candidates.append(node)
            for obj in candidates:
                if not isinstance(obj, dict):
                    continue
                typ = obj.get("@type")
                types = typ if isinstance(typ, list) else [typ]
                if not set(types) & {"NewsArticle", "BlogPosting", "Article"}:
                    continue
                title = obj.get("headline") or obj.get("name")
                url = obj.get("url") or obj.get("mainEntityOfPage")
                image = obj.get("image")
                if isinstance(image, list):
                    image = image[0]
                if isinstance(image, dict):
                    image = image.get("url")
                if title and url:
                    items.append({
                        "title": title,
                        "url": normalize_url(url, source["page_url"]),
                        "published_at": obj.get("datePublished") or obj.get("dateModified"),
                        "description": obj.get("description", ""),
                        "image_url": image or "",
                        "authors": obj.get("author", []),
                    })
    return items


def parse_anchor_listing(soup: BeautifulSoup, source: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        title = strip_html(anchor.get_text(" ", strip=True))
        if len(title) < 18:
            heading = anchor.find(["h1", "h2", "h3"])
            title = strip_html(heading.get_text(" ", strip=True)) if heading else title
        url = normalize_url(anchor["href"], source["page_url"])
        if not url or url in seen or source_domain(source["page_url"]) not in source_domain(url):
            continue
        if len(title) < 18 or len(title.split()) < 3:
            continue
        parent = anchor.find_parent(["article", "li", "div"]) or anchor
        date = ""
        time_tag = parent.find("time") if parent else None
        if time_tag:
            date = time_tag.get("datetime") or time_tag.get_text(" ", strip=True)
        img = parent.find("img") if parent else None
        desc = ""
        para = parent.find("p") if parent else None
        if para:
            desc = para.get_text(" ", strip=True)
        items.append({"title": title, "url": url, "published_at": date, "description": desc, "image_url": img.get("src", "") if img else ""})
        seen.add(url)
    return items


def source_domain(url: str) -> str:
    return normalize_url(url).split("/")[2].removeprefix("www.") if normalize_url(url) else ""


def fetch_hacker_news(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    ids = []
    for endpoint in ("topstories", "newstories", "showstories"):
        ids.extend(fetcher.get(f"https://hacker-news.firebaseio.com/v0/{endpoint}.json").json()[:20])
    items = []
    for story_id in list(dict.fromkeys(ids))[: source.get("max_items", 50)]:
        try:
            story = fetcher.get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json").json()
        except Exception:
            continue
        if not story or story.get("type") != "story":
            continue
        title = story.get("title", "")
        url = story.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        published = datetime.fromtimestamp(story.get("time", 0), tz=timezone.utc).isoformat()
        items.append({"id": f"hn-{story_id}", "title": title, "url": url, "published_at": published, "description": "Hacker News discussion", "authors": [story.get("by", "")]})
    return keyword_filter(items, {**source, "include_keywords": source.get("include_keywords") or ["AI", "machine learning", "LLM", "model", "GPU", "startup", "developer", "open source", "Show HN", "inference", "agent"]})


def fetch_arxiv(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    query = quote_plus(source["query"])
    url = f"https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={source.get('max_items', 12)}&sortBy=submittedDate&sortOrder=descending"
    parsed = feedparser.parse(fetcher.get(url).content)
    items = []
    for entry in parsed.entries:
        items.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published_at": entry.get("published"),
            "description": truncate(entry.get("summary"), 240),
            "authors": entry.get("authors", []),
            "research_track": source.get("research_track", ""),
        })
    return items


def fetch_europe_pmc(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    query = quote_plus('(Alzheimer OR dementia OR "mild cognitive impairment" OR neuroimaging OR MRI OR PET) AND ("machine learning" OR "deep learning" OR multimodal OR "foundation model" OR "artificial intelligence")')
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={query}&format=json&pageSize={source.get('max_items', 12)}&sort_date:y"
    data = fetcher.get(url).json()
    items = []
    for row in data.get("resultList", {}).get("result", []):
        link = row.get("doi")
        url = f"https://doi.org/{link}" if link else row.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url") or f"https://europepmc.org/article/{row.get('source','MED')}/{row.get('id')}"
        items.append({
            "id": f"epmc-{row.get('id')}",
            "title": row.get("title", ""),
            "url": url,
            "published_at": row.get("firstPublicationDate") or row.get("pubYear"),
            "description": truncate(row.get("abstractText"), 240),
            "authors": row.get("authorString", ""),
            "research_track": source.get("research_track", ""),
        })
    return items


def keyword_filter(items: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
    include = [str(x).lower() for x in source.get("include_keywords") or []]
    exclude = [str(x).lower() for x in source.get("exclude_keywords") or []]
    out = []
    for item in items:
        text = f"{item.get('title','')} {item.get('description','')}".lower()
        if include and not any(k.lower() in text for k in include):
            continue
        if exclude and any(k in text for k in exclude):
            continue
        out.append(item)
    return out
