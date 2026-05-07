from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from .config import BotConfig
from .http import api_get_json
from .ledger import load_daily_state, save_daily_state
from .markets import ASSET_PATTERNS, WINDOW_SECONDS


@dataclass
class RiskResult:
    ok: bool
    reason: Optional[str]
    amount_usd: float
    spent_today: float
    trades_today: int
    losses_today: int = 0
    cash_pnl_today: Optional[float] = None


ET = timezone(timedelta(hours=-4))


def _today_start_epoch() -> int:
    now = datetime.now(ET)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _guard_start_epoch(config: BotConfig) -> int:
    start_epoch = _today_start_epoch()
    raw = config.direct_guard_reset_utc
    if not raw:
        return start_epoch
    try:
        reset = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return start_epoch
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    return max(start_epoch, int(reset.timestamp()))


def _fast_market_start_epoch(slug: Optional[str]) -> Optional[int]:
    if not slug:
        return None
    try:
        return int(str(slug).rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return None


def _asset_title_patterns(asset: str) -> list[str]:
    return ASSET_PATTERNS.get(asset.upper(), [f"{asset.lower()} up or down"])


def _daily_asset_cash_snapshot(config: BotConfig, user_address: str) -> dict:
    params = urlencode({"user": user_address, "limit": 500, "offset": 0})
    rows = api_get_json(f"https://data-api.polymarket.com/activity?{params}", timeout=20)
    if not isinstance(rows, list):
        raise RuntimeError("Polymarket activity unavailable")

    start_epoch = _guard_start_epoch(config)
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    window_seconds = WINDOW_SECONDS.get(config.window, 300)
    cash_out = 0.0
    cash_in = 0.0
    by_market: dict[str, dict[str, float]] = {}

    for row in rows:
        try:
            ts = int(row.get("timestamp") or 0)
        except (TypeError, ValueError):
            continue
        if ts < start_epoch:
            continue
        title = str(row.get("title") or "").lower()
        if not any(pattern in title for pattern in _asset_title_patterns(config.asset)):
            continue
        slug = str(row.get("eventSlug") or row.get("marketSlug") or title)
        bucket = by_market.setdefault(slug, {"buy": 0.0, "redeem": 0.0})
        usdc = float(row.get("usdcSize") or 0.0)
        row_type = str(row.get("type") or "").upper()
        side = str(row.get("side") or "").upper()
        if row_type == "TRADE" and side == "BUY":
            cash_out += usdc
            bucket["buy"] += usdc
        elif row_type == "REDEEM":
            cash_in += usdc
            bucket["redeem"] += usdc

    losses = 0
    for slug, bucket in by_market.items():
        if bucket["buy"] <= 0 or bucket["redeem"] > 0:
            continue
        market_start = _fast_market_start_epoch(slug)
        ended = market_start is not None and now_epoch >= market_start + window_seconds + 60
        if ended:
            losses += 1

    return {
        "cash_pnl": round(cash_in - cash_out, 6),
        "losses": losses,
        "cash_in": round(cash_in, 6),
        "cash_out": round(cash_out, 6),
        "trade_count": sum(1 for bucket in by_market.values() if bucket["buy"] > 0),
    }


def _daily_btc_cash_snapshot(config: BotConfig, user_address: str) -> dict:
    return _daily_asset_cash_snapshot(config, user_address)


def check_and_size(config: BotConfig, requested_usd: float, user_address: Optional[str] = None) -> RiskResult:
    state = load_daily_state()
    spent = float(state.get("spent") or 0)
    trades = int(state.get("trades") or 0)

    cash_pnl_today = None
    losses_today = 0
    if user_address:
        try:
            snapshot = _daily_asset_cash_snapshot(config, user_address)
        except RuntimeError as exc:
            return RiskResult(False, f"live loss guard unavailable: {exc}", 0.0, spent, trades)
        synced_spent = max(spent, float(snapshot["cash_out"]))
        synced_trades = max(trades, int(snapshot["trade_count"]))
        if synced_spent != spent or synced_trades != trades:
            state["spent"] = round(synced_spent, 6)
            state["trades"] = synced_trades
            save_daily_state(state)
            spent = synced_spent
            trades = synced_trades
        cash_pnl_today = float(snapshot["cash_pnl"])
        losses_today = int(snapshot["losses"])
        if config.direct_max_daily_losses > 0 and losses_today >= config.direct_max_daily_losses:
            return RiskResult(
                False,
                f"daily loss limit reached ({losses_today}/{config.direct_max_daily_losses}, pnl={cash_pnl_today:+.2f})",
                0.0,
                spent,
                trades,
                losses_today,
                cash_pnl_today,
            )
        if config.direct_daily_stop_loss < 0 and cash_pnl_today <= config.direct_daily_stop_loss:
            return RiskResult(
                False,
                f"daily cash stop reached (pnl={cash_pnl_today:+.2f})",
                0.0,
                spent,
                trades,
                losses_today,
                cash_pnl_today,
            )

    if trades >= config.direct_max_daily_trades:
        return RiskResult(False, "daily trade limit reached", 0.0, spent, trades, losses_today, cash_pnl_today)

    remaining = float(config.direct_daily_budget) - spent
    if remaining <= 0:
        return RiskResult(False, "daily budget exhausted", 0.0, spent, trades, losses_today, cash_pnl_today)
    amount = min(float(requested_usd), remaining)
    if amount < 0.50:
        return RiskResult(False, "remaining budget too small", amount, spent, trades, losses_today, cash_pnl_today)
    return RiskResult(True, None, round(amount, 2), spent, trades, losses_today, cash_pnl_today)


def mark_live_success(amount_usd: float) -> None:
    state = load_daily_state()
    state["spent"] = round(float(state.get("spent") or 0) + float(amount_usd), 6)
    state["trades"] = int(state.get("trades") or 0) + 1
    save_daily_state(state)
