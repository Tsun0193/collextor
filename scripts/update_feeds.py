from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import feedparser
import yaml

from scripts.normalize import article_id, canonicalize_article, cluster_events, deduplicate, normalize_title, normalize_url, parse_date, parse_dt, strip_html, truncate, utc_now
from scripts.ranking import score_article
from scripts.source_adapters import Fetcher, fetch_source
from scripts.stocks import fetch_stocks
from scripts.validate_data import validate_dataset

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(rebuild_week: str | None = None, smoke: bool = False) -> dict[str, Any]:
    ranking_cfg = load_yaml(ROOT / "config" / "ranking.yaml")
    sources_cfg = load_yaml(ROOT / "config" / "sources.yaml")["sources"]
    now = utc_now()
    fetched_at = now.isoformat().replace("+00:00", "Z")
    fetcher = Fetcher()
    enabled = [s for s in sources_cfg if s.get("enabled")]
    by_id = {s["id"]: s for s in sources_cfg}
    statuses = []
    raw_items: list[dict[str, Any]] = []

    def one(source):
        started = utc_now()
        try:
            items = fetch_source(source, fetcher)
            return source, items, {
                "source_id": source["id"],
                "name": source["name"],
                "category": source.get("category", ""),
                "source_type": source.get("source_type", ""),
                "priority": source.get("priority", ""),
                "page_url": source.get("page_url", ""),
                "enabled": True,
                "ok": True,
                "last_successful_fetch": fetched_at,
                "last_attempt": fetched_at,
                "item_count": len(items),
                "message": "ok",
            }
        except Exception as exc:
            return source, [], {
                "source_id": source["id"],
                "name": source["name"],
                "category": source.get("category", ""),
                "source_type": source.get("source_type", ""),
                "priority": source.get("priority", ""),
                "page_url": source.get("page_url", ""),
                "enabled": True,
                "ok": False,
                "last_successful_fetch": None,
                "last_attempt": started.isoformat().replace("+00:00", "Z"),
                "item_count": 0,
                "message": str(exc)[:180],
            }

    workers = min(8, max(1, len(enabled)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, s) for s in enabled]
        for future in as_completed(futures):
            source, items, status = future.result()
            statuses.append(status)
            for raw in items:
                item = canonicalize_article(raw, source, fetched_at)
                if item:
                    raw_items.append(score_article(item, source, ranking_cfg, now=now))

    for source in sources_cfg:
        if not source.get("enabled"):
            statuses.append({
                "source_id": source["id"],
                "name": source["name"],
                "category": source.get("category", ""),
                "source_type": source.get("source_type", ""),
                "priority": source.get("priority", ""),
                "page_url": source.get("page_url", ""),
                "enabled": False,
                "ok": False,
                "last_successful_fetch": None,
                "last_attempt": fetched_at,
                "item_count": 0,
                "message": source.get("unavailable_reason", "disabled"),
            })

    deduped, duplicate_count = deduplicate(raw_items)
    cluster_events(deduped)
    multi_source_bonus = ranking_cfg["bonuses"].get("multi_source", 0)
    for item in deduped:
        if len(item.get("cluster_sources", [])) > 1 or item.get("also_covered_by"):
            item["score"] += multi_source_bonus
            item["score_reasons"].append("covered by multiple configured sources")

    previous_history = read_json(DATA / "history.json", {"articles": []}).get("articles", [])
    all_history, _ = deduplicate(deduped + previous_history)
    all_history = [item for item in all_history if article_quality_ok(item, by_id)]
    cutoff = now - timedelta(days=ranking_cfg["limits"]["history_days"])
    history = [i for i in all_history if (parse_dt(i.get("published_at")) or now) >= cutoff]
    history = sorted(history, key=lambda x: (x.get("published_at") or "", x.get("score", 0)), reverse=True)[: ranking_cfg["limits"]["history_max_items"]]

    daily_cutoff = now - timedelta(hours=ranking_cfg["limits"]["daily_hours"])
    latest_candidates = [i for i in history if (parse_dt(i.get("published_at")) or now) >= daily_cutoff and not by_id.get(i.get("source_id"), {}).get("weekly_only")]
    latest_articles = select_diverse_daily(latest_candidates, ranking_cfg)
    latest_articles = backfill_daily_sections(history, latest_articles, ranking_cfg, by_id, now)

    tz = ZoneInfo(ranking_cfg.get("timezone", "Asia/Bangkok"))
    local_now = now.astimezone(tz)
    latest = {
        "generated_at": fetched_at,
        "timezone": ranking_cfg.get("timezone"),
        "date_label": local_now.strftime("%A, %B %-d, %Y"),
        "articles": public_articles(latest_articles),
        "summary": {"successful_sources": sum(1 for s in statuses if s["ok"]), "failed_sources": sum(1 for s in statuses if s["enabled"] and not s["ok"]), "fetched_items": len(raw_items), "retained_items": len(latest_articles), "duplicates_removed": duplicate_count},
    }
    history_doc = {"generated_at": fetched_at, "articles": public_articles(history)}
    stocks_doc = fetch_stocks_safe(fetcher, fetched_at)
    stocks_doc["analysis"] = build_market_brief(stocks_doc, latest_articles)
    statuses.append(stock_source_status(stocks_doc, fetched_at))
    media_doc = fetch_media_safe(fetcher, fetched_at)
    statuses.extend(media_source_statuses(media_doc, fetched_at))
    source_status = {"generated_at": fetched_at, "sources": sorted(statuses, key=lambda x: x["name"].lower())}

    if not raw_items and previous_history:
        print("All source fetches failed; preserving previous article data and updating source status only.")
        atomic_write(DATA / "source-status.json", source_status)
        return {"ok": False, "reason": "all_fetches_failed_preserved_previous"}

    validate_dataset(latest)
    validate_dataset(history_doc)
    atomic_write(DATA / "latest.json", latest)
    atomic_write(DATA / "history.json", history_doc)
    atomic_write(DATA / "source-status.json", source_status)
    atomic_write(DATA / "stocks.json", stocks_doc)
    atomic_write(DATA / "media.json", media_doc)
    print(f"Sources ok={latest['summary']['successful_sources']} failed={latest['summary']['failed_sources']} fetched={len(raw_items)} retained_daily={len(latest_articles)} duplicates={duplicate_count} media={len(media_doc.get('items', []))}")
    if smoke:
        ok_sources = [s["name"] for s in statuses if s["ok"]]
        print("Live smoke successful sources: " + ", ".join(ok_sources[:10]))
    return {"ok": True}


def fetch_stocks_safe(fetcher: Fetcher, fetched_at: str) -> dict[str, Any]:
    previous = read_json(DATA / "stocks.json", {"generated_at": "", "source": {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/"}, "symbols": []})
    try:
        doc = fetch_stocks(load_yaml(ROOT / "config" / "stocks.yaml"), fetcher)
        if doc.get("symbols"):
            return doc
    except Exception as exc:
        previous["last_error"] = str(exc)[:180]
        previous["last_attempt"] = fetched_at
    return previous


def fetch_media_safe(fetcher: Fetcher, fetched_at: str) -> dict[str, Any]:
    previous = read_json(DATA / "media.json", {"generated_at": "", "source_note": "curated public feeds", "items": [], "source_status": []})
    try:
        cfg = load_yaml(ROOT / "config" / "media.yaml")
        doc = fetch_media(cfg, fetcher, fetched_at)
        if doc.get("items"):
            return doc
    except Exception as exc:
        previous["last_error"] = str(exc)[:180]
        previous["last_attempt"] = fetched_at
    return previous


def fetch_media(cfg: dict[str, Any], fetcher: Fetcher, fetched_at: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    statuses = []
    priority_weight = {"critical": 30, "high": 20, "medium": 10}
    for source in cfg.get("sources", []):
        if not source.get("enabled"):
            statuses.append(media_status(source, fetched_at, False, 0, source.get("unavailable_reason", "disabled")))
            continue
        try:
            parsed = feedparser.parse(fetcher.get(source["feed_url"]).content)
            if parsed.bozo and not parsed.entries:
                raise RuntimeError(f"Invalid feed: {parsed.bozo_exception}")
            count = 0
            for entry in parsed.entries[: source.get("max_items", 5)]:
                url = normalize_url(entry.get("link"), source.get("page_url"))
                title = strip_html(entry.get("title"))
                published = parse_date(entry.get("published") or entry.get("updated")) or fetched_at
                if not url or not title:
                    continue
                item = {
                    "id": entry.get("yt_videoid") or article_id(url, title),
                    "title": title[:240],
                    "url": url,
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "media_type": source.get("media_type", "Media"),
                    "published_at": published,
                    "description": truncate(entry.get("summary") or entry.get("description"), 180),
                    "image_url": normalize_url(youtube_thumbnail(entry) or media_image(entry), source.get("page_url")),
                    "score": priority_weight.get(source.get("priority"), 0),
                }
                items.append(item)
                count += 1
            statuses.append(media_status(source, fetched_at, True, count, "ok"))
        except Exception as exc:
            statuses.append(media_status(source, fetched_at, False, 0, str(exc)[:180]))
    selected = select_diverse_media(dedupe_media(items), cfg.get("max_items", 18))
    for item in selected:
        item.pop("score", None)
    return {
        "generated_at": fetched_at,
        "source_note": cfg.get("source_note", "curated public feeds"),
        "items": selected,
        "source_status": statuses,
    }


def media_image(entry: Any) -> str:
    for key in ("media_thumbnail", "media_content"):
        values = entry.get(key)
        if values:
            return values[0].get("url", "")
    return ""


def youtube_thumbnail(entry: Any) -> str:
    video_id = entry.get("yt_videoid")
    if not video_id:
        return ""
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"


def dedupe_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        keys = {normalize_url(item.get("url")) or item.get("id"), normalize_title(item.get("title"))}
        if seen & keys:
            continue
        seen.update(keys)
        out.append(item)
    return out


def select_diverse_media(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_source[item.get("source_id", "")].append(item)
    for bucket in by_source.values():
        bucket.sort(key=lambda item: item.get("published_at", ""), reverse=True)
    source_order = sorted(
        by_source,
        key=lambda source_id: (
            max((item.get("score", 0) for item in by_source[source_id]), default=0),
            by_source[source_id][0].get("published_at", "") if by_source[source_id] else "",
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(by_source.values()):
        for source_id in source_order:
            bucket = by_source[source_id]
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            if len(selected) >= limit:
                break
    return selected


def media_status(source: dict[str, Any], fetched_at: str, ok: bool, count: int, message: str) -> dict[str, Any]:
    return {
        "source_id": source["id"],
        "name": source["name"],
        "category": "media",
        "source_type": "rss",
        "priority": source.get("priority", ""),
        "page_url": source.get("page_url", ""),
        "enabled": bool(source.get("enabled")),
        "ok": ok,
        "last_successful_fetch": fetched_at if ok else None,
        "last_attempt": fetched_at,
        "item_count": count,
        "message": message,
    }


def media_source_statuses(media_doc: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    statuses = media_doc.get("source_status")
    if isinstance(statuses, list):
        return statuses
    return [{
        "source_id": "media_feeds",
        "name": "Media feeds",
        "category": "media",
        "source_type": "rss",
        "priority": "reference",
        "page_url": "",
        "enabled": True,
        "ok": bool(media_doc.get("items")),
        "last_successful_fetch": media_doc.get("generated_at") if media_doc.get("items") else None,
        "last_attempt": fetched_at,
        "item_count": len(media_doc.get("items", [])),
        "message": media_doc.get("last_error", "ok"),
    }]


def stock_source_status(stocks_doc: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    source = stocks_doc.get("source", {})
    ok = bool(stocks_doc.get("symbols"))
    return {
        "source_id": "market_yahoo_finance",
        "name": source.get("name", "Yahoo Finance"),
        "category": "markets",
        "source_type": "JSON",
        "priority": "reference",
        "page_url": source.get("url", "https://finance.yahoo.com/"),
        "enabled": True,
        "ok": ok,
        "last_successful_fetch": stocks_doc.get("generated_at") if ok else None,
        "last_attempt": fetched_at,
        "item_count": len(stocks_doc.get("symbols", [])),
        "message": source.get("note", "delayed market quotes") if ok else stocks_doc.get("last_error", "unavailable"),
    }


def build_market_brief(stocks_doc: dict[str, Any], articles: list[dict[str, Any]]) -> dict[str, Any]:
    symbols = stocks_doc.get("symbols", [])
    if not symbols:
        return {"headline": "Market context unavailable", "bullets": [], "related_articles": [], "disclaimer": "Delayed market data only. Not investment advice."}
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for quote in symbols:
        by_category[quote.get("category", "other")].append(quote)
    bullets = []
    chip_quotes = by_category.get("chips", [])
    if chip_quotes:
        avg = avg_change(chip_quotes)
        leaders = sorted(chip_quotes, key=lambda q: q.get("change_percent") or 0)
        weakest = leaders[0]
        strongest = leaders[-1]
        direction = "under pressure" if avg < -1 else "firmer" if avg > 1 else "mixed"
        bullets.append(f"Chip names look {direction}: the configured chip basket averages {avg:+.2f}%, with {weakest['symbol']} at {weakest.get('change_percent'):+.2f}% and {strongest['symbol']} at {strongest.get('change_percent'):+.2f}%.")
    platform_quotes = by_category.get("hyperscaler", []) + by_category.get("cloud", []) + by_category.get("lab", [])
    if platform_quotes:
        positives = [q for q in platform_quotes if (q.get("change_percent") or 0) > 0]
        negatives = [q for q in platform_quotes if (q.get("change_percent") or 0) < 0]
        bullets.append(f"AI platform stocks are split: {len(positives)} up and {len(negatives)} down across the tracked cloud/lab names.")
    movers = sorted(symbols, key=lambda q: abs(q.get("change_percent") or 0), reverse=True)[:3]
    bullets.append("Largest tracked moves: " + ", ".join(f"{q['symbol']} {q.get('change_percent'):+.2f}%" for q in movers) + ".")
    related = []
    for item in articles:
        if item.get("section") == "business_policy" and item.get("url"):
            related.append({"title": item["title"], "url": item["url"], "source_name": item["source_name"]})
        if len(related) >= 3:
            break
    return {
        "headline": market_headline(symbols),
        "bullets": bullets[:3],
        "related_articles": related,
        "disclaimer": "Delayed public market data and rule-based context only. Not investment advice.",
    }


def avg_change(quotes: list[dict[str, Any]]) -> float:
    changes = [q.get("change_percent") for q in quotes if isinstance(q.get("change_percent"), (int, float))]
    if not changes:
        return 0.0
    return round(sum(changes) / len(changes), 2)


def market_headline(symbols: list[dict[str, Any]]) -> str:
    avg = avg_change(symbols)
    if avg <= -3:
        return "AI market tape is risk-off today"
    if avg >= 3:
        return "AI market tape is broadly bid"
    return "AI market tape is mixed"


def public_articles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ["id", "title", "url", "canonical_url", "source_id", "source_name", "source_category", "section", "published_at", "fetched_at", "description", "image_url", "authors", "score", "score_reasons", "is_breaking", "is_must_read", "is_long_read", "research_track", "event_cluster_id", "also_covered_by", "cluster_sources"]
    articles = []
    for item in items:
        public = {}
        for key in keys:
            if key not in item and key not in {"authors", "score_reasons"}:
                continue
            value = item.get(key, [] if key in {"authors", "score_reasons", "also_covered_by", "cluster_sources"} else "")
            if key in {"url", "canonical_url", "image_url"}:
                value = normalize_url(value)
            public[key] = value
        articles.append(public)
    return articles


def select_diverse_daily(items: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    limit = cfg["limits"]["daily_visible_max"]
    default_cap = cfg["limits"].get("daily_source_max", 6)
    hn_cap = cfg["limits"].get("hacker_news_daily_max", 4)
    counts: dict[str, int] = {}
    selected = []
    for item in sorted(items, key=lambda x: (x.get("score", 0), x.get("published_at", "")), reverse=True):
        source_id = item.get("source_id", "")
        cap = hn_cap if source_id == "hacker_news" else default_cap
        if counts.get(source_id, 0) >= cap:
            continue
        selected.append(item)
        counts[source_id] = counts.get(source_id, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def backfill_daily_sections(history: list[dict[str, Any]], selected: list[dict[str, Any]], cfg: dict[str, Any], sources: dict[str, dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    max_items = cfg["limits"]["daily_visible_max"]
    backfill_cutoff = now - timedelta(days=cfg["limits"].get("daily_backfill_days", 7))
    chosen = list(selected)
    chosen_ids = {item["id"] for item in chosen}
    chosen_clusters = {item.get("event_cluster_id") or item["id"] for item in chosen}
    section_targets = cfg.get("section_min_items", {})
    for section, minimum in section_targets.items():
        current = sum(1 for item in chosen if item.get("section") == section)
        if current >= minimum:
            continue
        pool = []
        for item in history:
            published = parse_dt(item.get("published_at")) or now
            cluster = item.get("event_cluster_id") or item["id"]
            if item["id"] in chosen_ids or cluster in chosen_clusters:
                continue
            if item.get("section") != section:
                continue
            if sources.get(item.get("source_id"), {}).get("weekly_only"):
                continue
            if published < backfill_cutoff:
                continue
            pool.append(item)
        for item in sorted(pool, key=lambda x: (x.get("score", 0), x.get("published_at", "")), reverse=True):
            chosen.append(item)
            chosen_ids.add(item["id"])
            chosen_clusters.add(item.get("event_cluster_id") or item["id"])
            current += 1
            if current >= minimum or len(chosen) >= max_items:
                break
    return sorted(chosen, key=lambda x: (x.get("score", 0), x.get("published_at", "")), reverse=True)[:max_items]


def article_quality_ok(item: dict[str, Any], sources: dict[str, dict[str, Any]]) -> bool:
    source = sources.get(item.get("source_id"), {})
    title = (item.get("title") or "").strip().lower()
    url = item.get("canonical_url") or item.get("url") or ""
    if not title or title in {"skip to main content", "news", "blog", "research"}:
        return False
    if source.get("source_type") == "html" and item.get("published_at") == item.get("fetched_at"):
        return False
    if item.get("source_id") == "mistral_news" and "/products/" in url:
        return False
    if item.get("source_id") == "deepmind_blog" and "/models/" in url:
        return False
    if item.get("source_id") == "openai_news" and url.rstrip("/") == "https://openai.com/news":
        return False
    if item.get("source_id") == "hacker_news" and title.startswith("show hn:"):
        topical = ["ai", "llm", "agent", "machine learning", "model", "gpu", "developer", "open source", "inference", "rag", "mlops", "pytorch", "cuda", "multimodal"]
        if not any(topic_matches(title, term) for term in topical):
            return False
    if item.get("source_id") == "hacker_news":
        text = f"{title} {item.get('description', '').lower()}"
        topical = ["ai", "llm", "agent", "machine learning", "model", "gpu", "developer tool", "open source", "inference", "rag", "mlops", "pytorch", "cuda", "multimodal", "neural", "data center", "semiconductor"]
        if not any(topic_matches(text, term) for term in topical):
            return False
    return True


def topic_matches(text: str, term: str) -> bool:
    term = term.lower()
    if len(term) <= 4 and re.fullmatch(r"[a-z0-9+#.]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild-week", help="Reserved for manually rebuilding a selected ISO week from retained history.")
    parser.add_argument("--smoke", action="store_true", help="Print live source smoke-test summary.")
    args = parser.parse_args()
    run(args.rebuild_week, args.smoke)


if __name__ == "__main__":
    main()
