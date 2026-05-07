from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
SHADOW_LOG = ROOT / "logs" / "direct_shadow_decisions.jsonl"
ET = timezone(timedelta(hours=-4))
STAKE = 5.0


def parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def read_rows() -> list[dict]:
    if not SHADOW_LOG.exists():
        return []
    rows: list[dict] = []
    for line in SHADOW_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            row["_ts"] = parse_utc(row["timestamp_utc"])
        except (KeyError, ValueError):
            continue
        rows.append(row)
    return rows


def fetch_binance_closes(end_times: set[datetime]) -> dict[datetime, float]:
    if not end_times:
        return {}
    closes: dict[datetime, float] = {}
    start = min(end_times) - timedelta(minutes=1)
    final = max(end_times) + timedelta(minutes=2)
    current = start
    while current <= final:
        chunk_end = min(current + timedelta(minutes=999), final)
        url = "https://api.binance.com/api/v3/klines?" + urlencode(
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": int(current.timestamp() * 1000),
                "endTime": int(chunk_end.timestamp() * 1000),
                "limit": 1000,
            }
        )
        data = json.load(urlopen(url, timeout=15))
        for candle in data:
            ts = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc).replace(second=0, microsecond=0)
            closes[ts] = float(candle[4])
        current = chunk_end + timedelta(minutes=1)
    return closes


def market_outcome(row: dict, closes: dict[datetime, float]) -> str | None:
    market = row.get("market") or {}
    open_price = market.get("market_open_price")
    end_raw = market.get("end_time_utc")
    if open_price is None or not end_raw:
        return None
    end_time = parse_utc(end_raw).replace(second=0, microsecond=0)
    close_price = closes.get(end_time)
    if close_price is None:
        return None
    if close_price > float(open_price):
        return "yes"
    if close_price < float(open_price):
        return "no"
    return "push"


def entry_price(row: dict) -> float | None:
    side = row.get("side")
    market = row.get("market") or {}
    yes_price = market.get("market_yes_price")
    if side not in ("yes", "no") or yes_price is None:
        return None
    yes_price = float(yes_price)
    return yes_price if side == "yes" else 1.0 - yes_price


def pnl_for(price: float, won: bool) -> float:
    if not won:
        return -STAKE
    return STAKE * ((1.0 / price) - 1.0)


def summarize_late(rows: list[dict], closes: dict[datetime, float]) -> None:
    late = [r for r in rows if r.get("shadow_type") == "late_entry"]
    print(f"late shadow rows: {len(late)}")
    print("late status:", dict(Counter(r.get("status") for r in late).most_common()))
    print("late skip reasons:", dict(Counter(r.get("skip_reason") for r in late).most_common(12)))

    seen: dict[tuple[str, str | None], dict] = {}
    for row in sorted(late, key=lambda r: r["_ts"]):
        market = row.get("market") or {}
        key = (market.get("condition_id") or market.get("question") or "", row.get("side"))
        if key[0] and key not in seen:
            seen[key] = row

    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "pnl": 0.0})
    examples: list[tuple[datetime, str, str, float, bool, float]] = []
    for row in seen.values():
        price = entry_price(row)
        outcome = market_outcome(row, closes)
        side = row.get("side")
        if price is None or outcome is None or side not in ("yes", "no"):
            continue
        won = outcome == side
        pnl = pnl_for(price, won)
        key = row.get("skip_reason") or row.get("status") or "candidate"
        grouped[key]["n"] += 1
        grouped[key]["w"] += int(won)
        grouped[key]["l"] += int(not won)
        grouped[key]["pnl"] += pnl
        examples.append((row["_ts"], key, side, price, won, pnl))

    print("late unique market+side by reason:")
    for reason, stat in sorted(grouped.items(), key=lambda kv: kv[1]["pnl"], reverse=True):
        print(
            f"  {reason}: {int(stat['n'])} {int(stat['w'])}W/{int(stat['l'])}L "
            f"pnl={stat['pnl']:+.2f}"
        )
    print("latest late examples:")
    for ts, reason, side, price, won, pnl in examples[-10:]:
        print(f"  {ts.astimezone(ET):%m-%d %H:%M} {reason} {side.upper()}@{price:.3f} {'W' if won else 'L'} {pnl:+.2f}")


def summarize_exits(rows: list[dict]) -> None:
    exits = [r for r in rows if r.get("shadow_type") == "exit_snapshot"]
    print(f"exit snapshots: {len(exits)}")
    if not exits:
        return
    by_market: dict[str, dict] = {}
    for row in sorted(exits, key=lambda r: r["_ts"]):
        market = row.get("market") or {}
        key = market.get("condition_id") or market.get("question") or ""
        if key:
            by_market[key] = row
    print("latest exit snapshots by market:")
    for row in list(by_market.values())[-10:]:
        market = row.get("market") or {}
        position = row.get("position") or {}
        print(
            f"  {row['_ts'].astimezone(ET):%m-%d %H:%M} "
            f"{position.get('side')} avg={position.get('actual_avg_price')} "
            f"bid={row.get('current_bid')} exit_pnl={row.get('exit_pnl_at_bid')} "
            f"{market.get('question')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze direct live shadow observations.")
    parser.add_argument("--hours", type=float, default=24.0, help="Lookback window in hours.")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    rows = [r for r in read_rows() if r["_ts"] >= cutoff]
    end_times = {
        parse_utc((r.get("market") or {}).get("end_time_utc")).replace(second=0, microsecond=0)
        for r in rows
        if (r.get("market") or {}).get("end_time_utc")
    }
    closes = fetch_binance_closes(end_times)
    print(f"rows since {cutoff.astimezone(ET):%Y-%m-%d %H:%M ET}: {len(rows)}")
    summarize_late(rows, closes)
    summarize_exits(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
