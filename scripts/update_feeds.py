from __future__ import annotations

import argparse
import json
import os
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
    cutoff = now - timedelta(days=ranking_cfg["limits"]["history_days"])
    history = [i for i in all_history if (parse_dt(i.get("published_at")) or now) >= cutoff]
    history = sorted(history, key=lambda x: (x.get("published_at") or "", x.get("score", 0)), reverse=True)[: ranking_cfg["limits"]["history_max_items"]]

    daily_cutoff = now - timedelta(hours=ranking_cfg["limits"]["daily_hours"])
    latest_articles = [i for i in history if (parse_dt(i.get("published_at")) or now) >= daily_cutoff and not by_id.get(i.get("source_id"), {}).get("weekly_only")]
    latest_articles = sorted(latest_articles, key=lambda x: (x.get("score", 0), x.get("published_at", "")), reverse=True)[: ranking_cfg["limits"]["daily_visible_max"]]

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
    archive_index = update_archive_index(read_json(DATA / "archive-index.json", {"editions": []}), weekly)
    source_status = {"generated_at": fetched_at, "sources": sorted(statuses, key=lambda x: x["name"].lower())}
    history_doc = {"generated_at": fetched_at, "articles": public_articles(history)}

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
    atomic_write(DATA / "weekly" / f"{week_id}.json", weekly)
    atomic_write(DATA / "archive-index.json", archive_index)
    print(f"Sources ok={latest['summary']['successful_sources']} failed={latest['summary']['failed_sources']} fetched={len(raw_items)} retained_daily={len(latest_articles)} duplicates={duplicate_count} weekly={len(weekly_articles)}")
    if smoke:
        ok_sources = [s["name"] for s in statuses if s["ok"]]
        print("Live smoke successful sources: " + ", ".join(ok_sources[:10]))
    return {"ok": True, "week": week_id}


def public_articles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ["id", "title", "url", "canonical_url", "source_id", "source_name", "source_category", "section", "published_at", "fetched_at", "description", "image_url", "authors", "score", "score_reasons", "is_breaking", "is_must_read", "is_long_read", "research_track", "event_cluster_id", "also_covered_by", "cluster_sources"]
    return [{k: item.get(k, [] if k in {"authors", "score_reasons", "also_covered_by", "cluster_sources"} else "") for k in keys if k in item or k in {"authors", "score_reasons"}} for item in items]


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


def update_archive_index(index: dict[str, Any], weekly: dict[str, Any]) -> dict[str, Any]:
    editions = [e for e in index.get("editions", []) if e.get("week_id") != weekly["week_id"]]
    lead = next((a for a in weekly.get("articles", []) if a["id"] == weekly.get("lead")), {})
    editions.append({"week_id": weekly["week_id"], "date_range": weekly["date_range"], "lead_headline": lead.get("title", ""), "story_count": len(weekly.get("articles", [])), "url": f"weekly.html?week={weekly['week_id']}"})
    return {"generated_at": weekly["generated_at"], "editions": sorted(editions, key=lambda e: e["week_id"], reverse=True)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild-week", help="Reserved for manually rebuilding a selected ISO week from retained history.")
    parser.add_argument("--smoke", action="store_true", help="Print live source smoke-test summary.")
    args = parser.parse_args()
    run(args.rebuild_week, args.smoke)


if __name__ == "__main__":
    main()
