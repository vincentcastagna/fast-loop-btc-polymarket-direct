from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .http import api_get_json
from .time_utils import parse_fast_question_window, parse_iso_utc


WINDOW_SECONDS = {"5m": 300, "15m": 900}
ASSET_PATTERNS = {
    "BTC": ["bitcoin up or down"],
    "ETH": ["ethereum up or down"],
    "SOL": ["solana up or down"],
}
SLUG_PREFIX = {"BTC": "btc", "ETH": "eth", "SOL": "sol"}


@dataclass
class FastMarket:
    question: str
    slug: str
    condition_id: str
    end_time: datetime
    yes_token_id: str
    no_token_id: Optional[str]
    neg_risk: bool
    fee_rate_bps: int
    source: str

    @property
    def start_time(self) -> datetime:
        return self.end_time - timedelta(seconds=WINDOW_SECONDS.get("5m", 300))

    def remaining_seconds(self) -> float:
        return (self.end_time - datetime.now(timezone.utc)).total_seconds()


def _decode_tokens(raw: Any) -> List[str]:
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    else:
        data = raw or []
    return [str(token) for token in data if token]


def _add_market(target: List[FastMarket], seen: set, m: Dict[str, Any], asset: str, window: str, source_slug: str = "") -> None:
    question = m.get("question") or ""
    q = question.lower()
    slug = m.get("slug") or source_slug or ""
    if not any(pattern in q for pattern in ASSET_PATTERNS.get(asset, ASSET_PATTERNS["BTC"])):
        return
    if f"-{window}-" not in slug:
        return
    if m.get("closed", False):
        return

    end_time = (
        parse_iso_utc(m.get("endDate") or m.get("endDateIso") or m.get("end_date_iso") or "")
    )
    if not end_time:
        try:
            _, end_time = parse_fast_question_window(question)
        except ValueError:
            return
    now = datetime.now(timezone.utc)
    window_seconds = WINDOW_SECONDS.get(window, 300)
    if end_time < now - timedelta(seconds=window_seconds * 2):
        return
    if end_time > now + timedelta(hours=24):
        return

    tokens = _decode_tokens(m.get("clobTokenIds") or m.get("clob_token_ids"))
    if not tokens:
        return
    key = slug or m.get("conditionId") or tuple(tokens)
    if key in seen:
        return
    seen.add(key)
    target.append(
        FastMarket(
            question=question,
            slug=slug,
            condition_id=m.get("conditionId") or m.get("condition_id") or "",
            end_time=end_time,
            yes_token_id=tokens[0],
            no_token_id=tokens[1] if len(tokens) > 1 else None,
            neg_risk=bool(m.get("negRisk") or m.get("polymarket_neg_risk") or False),
            fee_rate_bps=int(float(m.get("fee_rate_bps") or m.get("feeRateBps") or 0)),
            source="gamma",
        )
    )


def discover_fast_markets(asset: str = "BTC", window: str = "5m", horizon_slots: int = 14) -> List[FastMarket]:
    asset = asset.upper()
    window_seconds = WINDOW_SECONDS.get(window, 300)
    markets: List[FastMarket] = []
    seen = set()
    prefix = SLUG_PREFIX.get(asset)

    if prefix:
        now = datetime.now(timezone.utc)
        base_start = int(now.timestamp() // window_seconds) * window_seconds
        for offset in range(-1, horizon_slots):
            event_slug = f"{prefix}-updown-{window}-{base_start + offset * window_seconds}"
            event = api_get_json(f"https://gamma-api.polymarket.com/events/slug/{quote(event_slug)}", timeout=5)
            if not isinstance(event, dict) or event.get("error"):
                continue
            for market in event.get("markets", []) or []:
                _add_market(markets, seen, market, asset, window, source_slug=event_slug)

    fallback = api_get_json(
        "https://gamma-api.polymarket.com/markets"
        "?limit=100&closed=false&tag=crypto&order=endDate&ascending=true",
        timeout=10,
    )
    if isinstance(fallback, list):
        for market in fallback:
            _add_market(markets, seen, market, asset, window)

    markets.sort(key=lambda m: m.end_time)
    return markets


def choose_live_market(markets: List[FastMarket], min_remaining: int, max_remaining: int, window: str) -> Optional[FastMarket]:
    now = datetime.now(timezone.utc)
    window_seconds = WINDOW_SECONDS.get(window, 300)
    candidates = []
    for market in markets:
        start = market.end_time - timedelta(seconds=window_seconds)
        remaining = (market.end_time - now).total_seconds()
        is_live = start <= now < market.end_time
        if not is_live:
            continue
        if remaining <= min_remaining:
            continue
        if max_remaining > 0 and remaining > max_remaining:
            continue
        candidates.append((remaining, market))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
