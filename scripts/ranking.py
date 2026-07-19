from __future__ import annotations

from datetime import timedelta
from typing import Any

from scripts.normalize import parse_dt, strip_html, utc_now


def contains_any(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    return [term for term in terms if term.lower() in low]


def score_article(item: dict[str, Any], source: dict[str, Any], cfg: dict[str, Any], now=None) -> dict[str, Any]:
    now = now or utc_now()
    text = " ".join([item.get("title", ""), item.get("description", ""), " ".join(item.get("authors", []))])
    score = cfg["source_weights"].get(source.get("priority", "low"), 2)
    reasons = [f"{source.get('priority', 'low')} source"]
    published = parse_dt(item.get("published_at"))
    if published:
        age = now - published
        if age <= timedelta(hours=6):
            score += cfg["recency"]["within_6_hours"]
            reasons.append("published within 6 hours")
        elif age <= timedelta(hours=24):
            score += cfg["recency"]["within_24_hours"]
            reasons.append("published within 24 hours")
        elif age <= timedelta(hours=48):
            score += cfg["recency"]["within_48_hours"]
            reasons.append("published within 48 hours")
        elif age > timedelta(days=7):
            score += cfg["recency"].get("older_than_7_days", 0)
            reasons.append("older than seven days")
    matches = contains_any(text, cfg.get("major_event_keywords", []))
    if matches:
        score += cfg["bonuses"]["major_event"]
        reasons.append("major-event keyword: " + ", ".join(matches[:3]))
    matches = contains_any(text, cfg.get("research_keywords", []))
    if matches:
        score += cfg["bonuses"]["research_keyword"]
        reasons.append("research relevance: " + ", ".join(matches[:3]))
    matches = contains_any(text, cfg.get("technical_keywords", []))
    if matches:
        score += cfg["bonuses"]["technical_keyword"]
        reasons.append("technical relevance: " + ", ".join(matches[:3]))
    matches = contains_any(text, cfg.get("startup_keywords", []))
    if matches:
        score += cfg["bonuses"]["startup_keyword"]
        reasons.append("startup/product signal: " + ", ".join(matches[:3]))
    matches = contains_any(text, cfg.get("negative_keywords", []))
    if matches:
        score += cfg["penalties"]["negative_signal"]
        reasons.append("negative signal: " + ", ".join(matches[:2]))
    if item.get("is_long_read") or source.get("category") == "long-read":
        score += cfg["bonuses"].get("long_read", 0)
        reasons.append("long-read source")
    item["score"] = int(score)
    item["score_reasons"] = reasons
    item["is_breaking"] = bool(published and now - published <= timedelta(hours=cfg["limits"]["breaking_hours"]) and score >= cfg["thresholds"]["breaking"])
    item["is_must_read"] = score >= cfg["thresholds"]["must_read"]
    item["is_long_read"] = bool(item.get("is_long_read") or source.get("category") in {"long-read", "analysis"})
    item["section"] = section_for(item, text)
    item["description"] = strip_html(item.get("description"))[:240]
    return item


def section_for(item: dict[str, Any], text: str) -> str:
    source_category = item.get("source_category", "")
    track = item.get("research_track", "")
    low = text.lower()
    if track == "medical_neuroimaging":
        return "medical_neuroimaging"
    if track == "multimodal_foundation" or source_category == "research":
        return "multimodal_foundation"
    if any(k in low for k in ["startup", "funding", "series a", "show hn", "launches", "developer tool", "acquisition"]):
        return "startup_product"
    if source_category in {"chips-infrastructure"} or any(k in low for k in ["chip", "gpu", "semiconductor", "data center", "regulation", "copyright", "policy", "cloud infrastructure", "nvidia"]):
        return "business_policy"
    if source_category in {"engineering", "infrastructure", "official-lab"} or any(k in low for k in ["inference", "pytorch", "cuda", "serving", "vllm", "agent", "evaluation", "open-weight"]):
        return "ai_engineering"
    if source_category in {"analysis", "long-read"}:
        return "long_reads"
    return "ai_engineering"


def weekly_eligible(item: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if item.get("research_track") and item.get("score", 0) >= cfg["thresholds"]["research_weekly"]:
        return True
    return item.get("score", 0) >= cfg["thresholds"]["weekly"] or item.get("is_long_read", False)
