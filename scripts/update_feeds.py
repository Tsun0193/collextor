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

import yaml

from scripts.normalize import canonicalize_article, cluster_events, deduplicate, parse_dt, utc_now
from scripts.ranking import score_article, weekly_eligible
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


def iso_week(dt: datetime) -> tuple[str, datetime, datetime]:
    monday = dt.date() - timedelta(days=dt.weekday())
    start = datetime.combine(monday, datetime.min.time(), tzinfo=dt.tzinfo)
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}", start, end


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
    week_id, week_start, week_end = iso_week(local_now)
    weekly_articles = [i for i in history if weekly_eligible(i, ranking_cfg) and week_start <= (parse_dt(i.get("published_at")) or now).astimezone(tz) <= week_end]
    weekly_articles = sorted(weekly_articles, key=lambda x: (x.get("score", 0), x.get("published_at", "")), reverse=True)[: ranking_cfg["limits"]["weekly_max_items"]]

    latest = {
        "generated_at": fetched_at,
        "timezone": ranking_cfg.get("timezone"),
        "date_label": local_now.strftime("%A, %B %-d, %Y"),
        "articles": public_articles(latest_articles),
        "summary": {"successful_sources": sum(1 for s in statuses if s["ok"]), "failed_sources": sum(1 for s in statuses if s["enabled"] and not s["ok"]), "fetched_items": len(raw_items), "retained_items": len(latest_articles), "duplicates_removed": duplicate_count},
    }
    weekly = make_weekly(week_id, week_start, week_end, weekly_articles, fetched_at)
    current_week = {"generated_at": fetched_at, "week_id": week_id, "path": f"data/weekly/{week_id}.json", "date_range": weekly["date_range"], "story_count": len(weekly_articles)}
    archive_index = build_archive_index(week_id, fetched_at)
    history_doc = {"generated_at": fetched_at, "articles": public_articles(history)}
    stocks_doc = fetch_stocks_safe(fetcher, fetched_at)
    stocks_doc["analysis"] = build_market_brief(stocks_doc, latest_articles)
    statuses.append(stock_source_status(stocks_doc, fetched_at))
    source_status = {"generated_at": fetched_at, "sources": sorted(statuses, key=lambda x: x["name"].lower())}

    if not raw_items and previous_history:
        print("All source fetches failed; preserving previous article data and updating source status only.")
        atomic_write(DATA / "source-status.json", source_status)
        return {"ok": False, "reason": "all_fetches_failed_preserved_previous"}

    validate_dataset(latest)
    validate_dataset(history_doc)
    validate_dataset(weekly)
    atomic_write(DATA / "latest.json", latest)
    atomic_write(DATA / "history.json", history_doc)
    atomic_write(DATA / "source-status.json", source_status)
    atomic_write(DATA / "stocks.json", stocks_doc)
    atomic_write(DATA / "weekly" / f"{week_id}.json", weekly)
    atomic_write(DATA / "current-week.json", current_week)
    atomic_write(DATA / "archive-index.json", archive_index)
    print(f"Sources ok={latest['summary']['successful_sources']} failed={latest['summary']['failed_sources']} fetched={len(raw_items)} retained_daily={len(latest_articles)} duplicates={duplicate_count} weekly={len(weekly_articles)}")
    if smoke:
        ok_sources = [s["name"] for s in statuses if s["ok"]]
        print("Live smoke successful sources: " + ", ".join(ok_sources[:10]))
    return {"ok": True, "week": week_id}


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
    return [{k: item.get(k, [] if k in {"authors", "score_reasons", "also_covered_by", "cluster_sources"} else "") for k in keys if k in item or k in {"authors", "score_reasons"}} for item in items]


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


def make_weekly(week_id: str, start: datetime, end: datetime, articles: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    by_section = defaultdict(list)
    for item in articles:
        by_section[item.get("section", "ai_engineering")].append(item["id"])
    return {
        "generated_at": generated_at,
        "week_id": week_id,
        "date_range": f"{start.strftime('%b %-d')} - {end.strftime('%b %-d, %Y')}",
        "lead": articles[0]["id"] if articles else "",
        "sections": dict(by_section),
        "articles": public_articles(articles),
    }


def build_archive_index(current_week_id: str, generated_at: str) -> dict[str, Any]:
    editions = []
    for path in (DATA / "weekly").glob("*.json"):
        try:
            weekly = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if weekly.get("week_id") == current_week_id:
            continue
        lead = next((a for a in weekly.get("articles", []) if a["id"] == weekly.get("lead")), {})
        editions.append({"week_id": weekly["week_id"], "date_range": weekly["date_range"], "lead_headline": lead.get("title", ""), "story_count": len(weekly.get("articles", [])), "url": f"weekly.html?week={weekly['week_id']}"})
    return {"generated_at": generated_at, "editions": sorted(editions, key=lambda e: e["week_id"], reverse=True)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild-week", help="Reserved for manually rebuilding a selected ISO week from retained history.")
    parser.add_argument("--smoke", action="store_true", help="Print live source smoke-test summary.")
    args = parser.parse_args()
    run(args.rebuild_week, args.smoke)


if __name__ == "__main__":
    main()
