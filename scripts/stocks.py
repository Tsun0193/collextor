from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from scripts.normalize import normalize_url, utc_now
from scripts.source_adapters import Fetcher


def fetch_stocks(config: dict[str, Any], fetcher: Fetcher) -> dict[str, Any]:
    source = config["source"]
    fetched_at = utc_now().isoformat().replace("+00:00", "Z")
    quotes = []
    for item in config.get("symbols", []):
        symbol = item["symbol"].upper()
        url = f"{source['quote_url'].rstrip('/')}/{symbol}?range=5d&interval=1d"
        data = fetcher.get(url).json()
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            continue
        meta = result.get("meta", {})
        price = parse_float(meta.get("regularMarketPrice"))
        previous_close = parse_float(meta.get("chartPreviousClose") or meta.get("previousClose"))
        if price is None:
            continue
        change = None
        change_percent = None
        if previous_close:
            change = round(price - previous_close, 4)
            change_percent = round((change / previous_close) * 100, 2)
        market_time = parse_market_time(meta.get("regularMarketTime"))
        quotes.append({
            "symbol": symbol,
            "name": item.get("name") or meta.get("longName") or symbol,
            "category": item.get("category", ""),
            "price": price,
            "currency": meta.get("currency") or "USD",
            "change": change,
            "change_percent": change_percent,
            "date": market_time[:10] if market_time else "",
            "time": market_time,
            "high": parse_float(meta.get("regularMarketDayHigh")),
            "low": parse_float(meta.get("regularMarketDayLow")),
            "volume": parse_int(meta.get("regularMarketVolume")),
            "url": normalize_url(f"https://finance.yahoo.com/quote/{symbol}/"),
        })
    return {
        "generated_at": fetched_at,
        "source": {"name": source["name"], "url": source["page_url"], "note": source.get("note", "")},
        "symbols": quotes,
    }


def parse_market_time(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return ""


def parse_float(value: Any) -> float | None:
    if value in (None, "", "N/D"):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def parse_int(value: Any) -> int | None:
    if value in (None, "", "N/D"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
