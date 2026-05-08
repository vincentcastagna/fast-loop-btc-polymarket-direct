from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from direct_fastloop.config import ROOT, load_config
from direct_fastloop.http import api_get_json


ET = timezone(timedelta(hours=-4))
LOG_PATHS = (ROOT / "logs" / "direct_decisions.jsonl", ROOT / "logs" / "direct_shadow_decisions.jsonl")


def parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_skip_rows(cutoff: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in LOG_PATHS:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(row.get("timestamp_utc"))
            if not ts or ts < cutoff:
                continue
            if row.get("status") != "skipped" and not str(row.get("status") or "").startswith("shadow_late_skipped"):
                continue
            if row.get("side") not in {"yes", "no"}:
                continue
            market = row.get("market") or {}
            if not market.get("slug"):
                continue
            rows.append(row)
    return rows


def gamma_outcome(slug: str) -> str | None:
    data = api_get_json(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=10)
    event = data[0] if isinstance(data, list) and data else None
    market = (event.get("markets") or [None])[0] if event else None
    if not market:
        return None
    try:
        prices = json.loads(market.get("outcomePrices") or "[]")
        up = float(prices[0])
        down = float(prices[1])
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        return None
    if up == down:
        return None
    return "yes" if up > down else "no"


def entry_price_for_side(row: dict[str, Any]) -> float | None:
    entry = float_or_none(row.get("entry_price"))
    if entry is not None:
        return entry
    market = row.get("market") or {}
    if row.get("side") == "yes":
        return float_or_none(market.get("market_yes_price"))
    no_price = float_or_none(market.get("market_no_price"))
    if no_price is not None:
        return no_price
    yes_price = float_or_none(market.get("market_yes_price"))
    if yes_price is not None:
        return 1.0 - yes_price
    return None


def trade_pnl(row: dict[str, Any], outcome: str, amount: float) -> tuple[bool, float] | None:
    side = row.get("side")
    entry = entry_price_for_side(row)
    if side not in {"yes", "no"} or outcome not in {"yes", "no"} or entry is None or entry <= 0:
        return None
    won = side == outcome
    return won, (float(amount) / entry - float(amount)) if won else -float(amount)


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        market = row.get("market") or {}
        key = (str(market.get("slug")), str(row.get("side")), str(row.get("skip_reason") or ""))
        if key not in by_key or str(row.get("timestamp_utc")) < str(by_key[key].get("timestamp_utc")):
            by_key[key] = row
    return list(by_key.values())


def strict_no_match(config, row: dict[str, Any]) -> bool:
    if row.get("side") != "no":
        return False
    momentum = row.get("momentum") or {}
    entry = entry_price_for_side(row)
    signal = float_or_none(row.get("signal_score"))
    setup = float_or_none(row.get("setup_score"))
    trend = float_or_none(momentum.get("trend_ratio"))
    volume = float_or_none(momentum.get("volume_ratio"))
    recent = float_or_none(momentum.get("recent_move_pct"))
    one_min = float_or_none(momentum.get("one_min_move_pct"))
    checks = [
        signal is not None and signal >= config.direct_live_strict_no_min_signal_score,
        setup is not None and setup >= config.direct_live_strict_no_min_setup_score,
        trend is not None and trend >= config.direct_live_strict_no_min_trend_ratio,
        volume is not None and volume >= config.direct_live_strict_no_min_volume_ratio,
        entry is not None and entry <= config.direct_live_strict_no_max_entry_price,
        recent is not None and recent <= config.direct_live_strict_no_max_recent_move_pct,
        one_min is not None and one_min <= config.direct_live_strict_no_max_one_min_move_pct,
    ]
    return all(checks)


def summarize(label: str, rows: list[dict[str, Any]], outcomes: dict[str, str | None], amount: float, examples: int) -> None:
    scored = []
    for row in rows:
        slug = (row.get("market") or {}).get("slug")
        result = trade_pnl(row, outcomes.get(slug), amount)
        if result is None:
            continue
        won, pnl = result
        scored.append((row, won, pnl))
    wins = sum(1 for _, won, _ in scored if won)
    losses = sum(1 for _, won, _ in scored if not won)
    pnl_total = sum(pnl for _, _, pnl in scored)
    avg = pnl_total / len(scored) if scored else 0.0
    print(f"{label}: {len(scored)} scored, {wins}W/{losses}L, pnl={pnl_total:+.2f}, avg={avg:+.2f}")
    for row, won, pnl in scored[:examples]:
        ts = parse_ts(row.get("timestamp_utc"))
        when = ts.astimezone(ET).strftime("%m-%d %H:%M") if ts else "?"
        market = row.get("market") or {}
        momentum = row.get("momentum") or {}
        print(
            "  ",
            when,
            row.get("side"),
            "WIN" if won else "LOSS",
            f"entry={entry_price_for_side(row):.3f}",
            f"pnl={pnl:+.2f}",
            f"sig={float_or_none(row.get('signal_score'))}",
            f"setup={float_or_none(row.get('setup_score'))}",
            f"trend={float_or_none(momentum.get('trend_ratio'))}",
            f"vol={float_or_none(momentum.get('volume_ratio'))}",
            str(market.get("question") or "")[:70],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Score skipped direct FastLoop candidates against resolved Gamma outcomes.")
    parser.add_argument("--hours", type=float, default=72.0, help="Lookback window when --since-utc is omitted.")
    parser.add_argument("--since-utc", help="ISO UTC cutoff, for example 2026-05-05T12:13:16Z.")
    parser.add_argument("--amount", type=float, default=5.0, help="Hypothetical amount per skipped trade.")
    parser.add_argument("--examples", type=int, default=5)
    args = parser.parse_args()

    config = load_config()
    if args.since_utc:
        cutoff = parse_ts(args.since_utc)
        if cutoff is None:
            raise SystemExit(f"Invalid --since-utc: {args.since_utc}")
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=float(args.hours))

    rows = dedupe(read_skip_rows(cutoff))
    slugs = sorted({(row.get("market") or {}).get("slug") for row in rows})
    outcomes: dict[str, str | None] = {}
    for idx, slug in enumerate(slugs, start=1):
        outcomes[str(slug)] = gamma_outcome(str(slug))
        if idx % 30 == 0:
            time.sleep(0.1)

    print(f"cutoff_utc={cutoff.isoformat()} rows={len(rows)} resolved={sum(1 for v in outcomes.values() if v)}")
    by_bucket: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bucket[(str(row.get("side")), str(row.get("skip_reason") or ""))].append(row)

    for (side, reason), bucket_rows in sorted(by_bucket.items(), key=lambda item: len(item[1]), reverse=True)[:20]:
        summarize(f"{side} / {reason}", bucket_rows, outcomes, args.amount, examples=min(args.examples, 2))

    strict_rows = [row for row in rows if strict_no_match(config, row)]
    summarize("STRICT_NO_POLICY", strict_rows, outcomes, float(config.direct_live_strict_no_amount_usd), args.examples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
