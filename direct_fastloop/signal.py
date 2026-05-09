from __future__ import annotations

import math
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .chainlink import observe_latest_sample, read_samples
from .http import api_get_json


ASSET_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}
SECONDS_PER_YEAR = 31_536_000


@dataclass
class Momentum:
    asset: str
    symbol: str
    price_now: float
    price_then: float
    momentum_pct: float
    direction: str
    avg_volume: float
    latest_volume: float
    volume_ratio: float
    trend_ratio: float
    one_min_move_pct: float
    two_min_move_pct: float
    recent_move_pct: float
    last_candle_move_pct: float
    close_location: float
    reversal_pct: float
    candles: int
    source: str = "binance"
    source_detail: Optional[str] = None
    feed_updated_at_utc: Optional[str] = None
    feed_age_seconds: Optional[float] = None


def momentum_to_dict(momentum: Momentum | Dict[str, Any]) -> Dict[str, Any]:
    if is_dataclass(momentum):
        return asdict(momentum)
    return dict(momentum)


def get_binance_momentum(asset: str = "BTC", lookback_minutes: int = 5) -> Optional[Momentum]:
    """Exact Binance momentum calculation from the current FastLoop bot."""
    asset = asset.upper()
    symbol = ASSET_SYMBOLS.get(asset, "BTCUSDT")
    candle_limit = max(lookback_minutes + 2, 8)
    result = api_get_json(
        "https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval=1m&limit={candle_limit}",
        timeout=10,
    )
    if not result or isinstance(result, dict):
        return None

    try:
        candles = result[-max(lookback_minutes, 2):]
        if len(candles) < 2:
            return None

        closes = [float(c[4]) for c in candles]
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        price_then = float(candles[0][1])
        price_now = closes[-1]
        momentum_pct = ((price_now - price_then) / price_then) * 100
        direction = "up" if momentum_pct > 0 else "down"
        direction_sign = 1 if momentum_pct >= 0 else -1

        volumes = [float(c[5]) for c in candles]
        avg_volume = sum(volumes) / len(volumes)
        latest_volume = volumes[-1]
        volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 1.0
        trend_ratio = (
            sum(1 for delta in deltas if delta * direction_sign > 0) / len(deltas)
            if deltas else 0.0
        )

        if len(closes) >= 3:
            recent_anchor = closes[-3]
            recent_move_pct = ((closes[-1] - recent_anchor) / recent_anchor) * 100
        else:
            recent_move_pct = momentum_pct
        two_min_move_pct = recent_move_pct

        if len(closes) >= 2:
            one_min_anchor = closes[-2]
            one_min_move_pct = ((closes[-1] - one_min_anchor) / one_min_anchor) * 100
        else:
            one_min_move_pct = momentum_pct

        last_candle_move_pct = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if len(closes) >= 2 else 0.0

        session_high = max(highs)
        session_low = min(lows)
        session_range = max(session_high - session_low, 1e-9)
        if direction == "up":
            close_location = (price_now - session_low) / session_range
        else:
            close_location = (session_high - price_now) / session_range

        reversal_pct = 0.0
        if momentum_pct != 0 and last_candle_move_pct * momentum_pct < 0:
            reversal_pct = min(1.0, abs(last_candle_move_pct) / abs(momentum_pct))

        return Momentum(
            asset=asset,
            symbol=symbol,
            price_now=price_now,
            price_then=price_then,
            momentum_pct=momentum_pct,
            direction=direction,
            avg_volume=avg_volume,
            latest_volume=latest_volume,
            volume_ratio=volume_ratio,
            trend_ratio=trend_ratio,
            one_min_move_pct=one_min_move_pct,
            two_min_move_pct=two_min_move_pct,
            recent_move_pct=recent_move_pct,
            last_candle_move_pct=last_candle_move_pct,
            close_location=close_location,
            reversal_pct=reversal_pct,
            candles=len(candles),
            source="binance",
        )
    except (IndexError, ValueError, KeyError, ZeroDivisionError):
        return None


def _wants_chainlink(source: str) -> bool:
    return str(source or "").lower() in {"chainlink", "chainlink_primary", "chainlink-first", "chainlink_first"}


def _sample_gap_ok(rows: list[dict[str, Any]], max_gap_seconds: int) -> bool:
    if max_gap_seconds <= 0 or len(rows) < 2:
        return True
    for previous, current in zip(rows, rows[1:]):
        gap = (current["_observed_at"] - previous["_observed_at"]).total_seconds()
        if gap > max_gap_seconds:
            return False
    return True


def get_chainlink_momentum(
    asset: str = "BTC",
    lookback_minutes: int = 5,
    *,
    rpc_url: Optional[str] = None,
    feed_address: Optional[str] = None,
    max_feed_age_seconds: int = 180,
    min_samples: int = 3,
    max_sample_gap_seconds: int = 150,
) -> Optional[Momentum]:
    """Build local momentum from Chainlink latestRoundData samples.

    Chainlink Data Feeds expose current oracle rounds, not historical candles.
    The bot samples latestRoundData on each scheduled run, then computes the
    same momentum shape from recent local samples. Binance remains the fallback
    while the sample window is warming up or if the feed/RPC is stale.
    """
    asset = asset.upper()
    latest = observe_latest_sample(
        asset,
        rpc_url=rpc_url,
        feed_address=feed_address,
        max_feed_age_seconds=max_feed_age_seconds,
    )
    if latest is None:
        return None

    now = datetime.now(timezone.utc)
    lookback_seconds = max(60, int(lookback_minutes * 60))
    cutoff = now - timedelta(seconds=lookback_seconds)
    rows = read_samples(asset)
    rows = [
        row
        for row in rows
        if row["_observed_at"] >= now - timedelta(seconds=max(lookback_seconds * 2, 900))
    ]
    if len(rows) < max(2, min_samples):
        return None

    current = rows[-1]
    previous_candidates = [row for row in rows if row["_observed_at"] <= cutoff]
    previous = previous_candidates[-1] if previous_candidates else rows[0]
    observed_span = (current["_observed_at"] - previous["_observed_at"]).total_seconds()
    if observed_span < min(lookback_seconds * 0.55, 90):
        return None

    window_rows = [row for row in rows if row["_observed_at"] >= previous["_observed_at"]]
    if not _sample_gap_ok(window_rows, max_sample_gap_seconds):
        return None

    prices = [float(row["_price"]) for row in window_rows]
    if len(prices) < 2 or previous["_price"] <= 0:
        return None

    price_then = float(previous["_price"])
    price_now = float(current["_price"])
    momentum_pct = ((price_now - price_then) / price_then) * 100
    direction = "up" if momentum_pct > 0 else "down"
    direction_sign = 1 if momentum_pct >= 0 else -1
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    trend_ratio = (
        sum(1 for delta in deltas if delta * direction_sign > 0) / len(deltas)
        if deltas else 0.0
    )

    if len(prices) >= 3:
        recent_anchor = prices[-3]
        recent_move_pct = ((price_now - recent_anchor) / recent_anchor) * 100 if recent_anchor > 0 else momentum_pct
    else:
        recent_move_pct = momentum_pct
    two_min_move_pct = recent_move_pct

    one_min_anchor = prices[-2]
    one_min_move_pct = ((price_now - one_min_anchor) / one_min_anchor) * 100 if one_min_anchor > 0 else momentum_pct
    last_candle_move_pct = one_min_move_pct

    session_high = max(prices)
    session_low = min(prices)
    session_range = max(session_high - session_low, 1e-9)
    if direction == "up":
        close_location = (price_now - session_low) / session_range
    else:
        close_location = (session_high - price_now) / session_range

    reversal_pct = 0.0
    if momentum_pct != 0 and last_candle_move_pct * momentum_pct < 0:
        reversal_pct = min(1.0, abs(last_candle_move_pct) / abs(momentum_pct))

    return Momentum(
        asset=asset,
        symbol=f"CHAINLINK:{asset}/USD",
        price_now=price_now,
        price_then=price_then,
        momentum_pct=momentum_pct,
        direction=direction,
        avg_volume=0.0,
        latest_volume=0.0,
        volume_ratio=0.7,
        trend_ratio=trend_ratio,
        one_min_move_pct=one_min_move_pct,
        two_min_move_pct=two_min_move_pct,
        recent_move_pct=recent_move_pct,
        last_candle_move_pct=last_candle_move_pct,
        close_location=close_location,
        reversal_pct=reversal_pct,
        candles=len(window_rows),
        source="chainlink",
        source_detail=f"{latest.feed_address} round {latest.round_id}",
        feed_updated_at_utc=latest.updated_at.isoformat(),
        feed_age_seconds=round(latest.age_seconds, 3),
    )


def get_signal_momentum(config) -> Optional[Momentum]:
    if getattr(config, "chainlink_enabled", False) and _wants_chainlink(getattr(config, "signal_source", "")):
        momentum = get_chainlink_momentum(
            config.asset,
            config.lookback_minutes,
            rpc_url=getattr(config, "chainlink_rpc_url", None),
            feed_address=getattr(config, "chainlink_feed_address", None),
            max_feed_age_seconds=getattr(config, "chainlink_max_feed_age_seconds", 180),
            min_samples=getattr(config, "chainlink_min_samples", 3),
            max_sample_gap_seconds=getattr(config, "chainlink_max_sample_gap_seconds", 150),
        )
        if momentum:
            return momentum
    return get_binance_momentum(config.asset, config.lookback_minutes)


def warm_signal_source(config) -> None:
    if not (getattr(config, "chainlink_enabled", False) and _wants_chainlink(getattr(config, "signal_source", ""))):
        return
    observe_latest_sample(
        config.asset,
        rpc_url=getattr(config, "chainlink_rpc_url", None),
        feed_address=getattr(config, "chainlink_feed_address", None),
        max_feed_age_seconds=getattr(config, "chainlink_max_feed_age_seconds", 180),
    )


def get_binance_price_at(asset: str, boundary_utc: datetime) -> Optional[float]:
    """Exact market-open reference fetch: close of the candle at startTime."""
    symbol = ASSET_SYMBOLS.get(asset.upper(), "BTCUSDT")
    start_ms = int(boundary_utc.timestamp() * 1000)
    result = api_get_json(
        "https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval=1m&startTime={start_ms}&limit=1",
        timeout=10,
    )
    if isinstance(result, list) and len(result) > 0:
        try:
            return float(result[0][4])
        except (IndexError, ValueError, TypeError):
            return None
    return None


def get_chainlink_price_near(asset: str, boundary_utc: datetime, max_gap_seconds: int = 150) -> Optional[float]:
    rows = read_samples(asset.upper())
    if not rows:
        return None
    best = min(rows, key=lambda row: abs((row["_observed_at"] - boundary_utc).total_seconds()))
    gap = abs((best["_observed_at"] - boundary_utc).total_seconds())
    if max_gap_seconds > 0 and gap > max_gap_seconds:
        return None
    return float(best["_price"])


def get_reference_price_at(config, boundary_utc: datetime, meta: Optional[dict[str, Any]] = None) -> Optional[float]:
    if getattr(config, "chainlink_enabled", False) and _wants_chainlink(getattr(config, "signal_source", "")):
        price = get_chainlink_price_near(
            config.asset,
            boundary_utc,
            max_gap_seconds=getattr(config, "chainlink_max_sample_gap_seconds", 150),
        )
        if price is not None:
            if meta is not None:
                meta["market_open_price_source"] = "chainlink"
            return price
    if meta is not None:
        meta["market_open_price_source"] = "binance"
    return get_binance_price_at(config.asset, boundary_utc)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_signed(value: float, scale: float, cap: float = 1.0) -> float:
    if scale <= 0:
        return 0.0
    return _clamp(value / scale, -cap, cap)


def compute_signal_score(momentum: Momentum | Dict[str, Any], min_momentum_pct: float = 0.0) -> float:
    """Exact scoring formula from the current FastLoop bot."""
    m = momentum_to_dict(momentum)
    abs_momentum = abs(m.get("momentum_pct", 0.0))
    recent_move = m.get("recent_move_pct", 0.0)
    last_move = m.get("last_candle_move_pct", 0.0)
    trend_ratio = m.get("trend_ratio", 0.0)
    close_location = m.get("close_location", 0.0)
    volume_ratio = m.get("volume_ratio", 1.0)
    reversal_pct = m.get("reversal_pct", 0.0)
    direction_sign = 1 if m.get("direction") == "up" else -1

    momentum_component = min(1.0, abs_momentum / max(min_momentum_pct, 0.20))
    recent_component = min(1.0, max(0.0, direction_sign * recent_move) / max(abs_momentum, 0.05))
    last_candle_component = 1.0 if direction_sign * last_move > 0 else 0.0
    volume_component = min(1.0, max(0.0, volume_ratio - 0.7) / 0.9)
    reversal_penalty = min(1.0, reversal_pct)

    score = (
        0.28 * momentum_component
        + 0.20 * trend_ratio
        + 0.20 * close_location
        + 0.14 * recent_component
        + 0.10 * last_candle_component
        + 0.08 * volume_component
        - 0.18 * reversal_penalty
    )
    return max(0.0, min(1.0, score))


def infer_move_side(move_pct: float, min_abs_move: float) -> Optional[str]:
    threshold = max(min_abs_move, 1e-9)
    if move_pct >= threshold:
        return "yes"
    if move_pct <= -threshold:
        return "no"
    return None


def estimate_path_aware_yes_prob(
    momentum: Momentum | Dict[str, Any],
    remaining: float,
    strike_distance_pct: Optional[float] = None,
    *,
    min_momentum_pct: float = 0.0,
    min_confirmation_pct: float = 0.02,
    min_strike_distance_pct: float = 0.05,
    min_time_remaining: int = 45,
    max_time_remaining: int = 150,
    window_seconds: int = 300,
) -> float:
    """Exact probability formula from the current FastLoop bot."""
    m = momentum_to_dict(momentum)
    direction_sign = 1 if m.get("direction") == "up" else -1
    reversal_scale = 1.0 - 0.35 * _clamp(m.get("reversal_pct", 0.0), 0.0, 1.0)

    strike_bias = 0.0
    if strike_distance_pct is not None:
        strike_bias = 0.20 * _normalize_signed(
            strike_distance_pct,
            max(min_strike_distance_pct * 2.0, 0.14),
            cap=1.25,
        )

    momentum_bias = 0.09 * _normalize_signed(
        m.get("momentum_pct", 0.0),
        max(min_momentum_pct * 2.0, 0.12),
        cap=1.2,
    )
    recent_bias = 0.07 * _normalize_signed(m.get("recent_move_pct", 0.0), 0.10, cap=1.1)
    one_min_bias = 0.04 * _normalize_signed(
        m.get("one_min_move_pct", 0.0),
        max(min_confirmation_pct * 2.0, 0.04),
        cap=1.2,
    )
    two_min_bias = 0.05 * _normalize_signed(
        m.get("two_min_move_pct", 0.0),
        max(min_confirmation_pct * 3.0, 0.06),
        cap=1.2,
    )
    trend_bias = 0.08 * direction_sign * _clamp((m.get("trend_ratio", 0.0) - 0.5) / 0.30, 0.0, 1.0)
    close_bias = 0.06 * direction_sign * _clamp((m.get("close_location", 0.0) - 0.5) / 0.30, 0.0, 1.0)

    window_upper = max_time_remaining if max_time_remaining > 0 else window_seconds
    time_span = max(1.0, window_upper - min_time_remaining)
    time_center = min_time_remaining + (time_span / 2)
    timing_multiplier = 0.85 + 0.15 * (
        1.0 - _clamp(abs(remaining - time_center) / max(time_span / 2, 1.0), 0.0, 1.0)
    )

    path_bias = (
        momentum_bias
        + recent_bias
        + one_min_bias
        + two_min_bias
        + trend_bias
        + close_bias
    ) * reversal_scale * timing_multiplier

    return _clamp(0.5 + strike_bias + path_bias, 0.03, 0.97)


def compute_setup_score(
    momentum: Momentum | Dict[str, Any],
    remaining: float,
    market_yes_price: float,
    strike_distance_pct: Optional[float],
    model_edge: float,
    *,
    min_momentum_pct: float = 0.0,
    min_strike_distance_pct: float = 0.05,
    min_time_remaining: int = 45,
    max_time_remaining: int = 150,
    window_seconds: int = 300,
) -> float:
    """Exact setup-score formula from the current FastLoop bot."""
    m = momentum_to_dict(momentum)
    window_upper = max_time_remaining if max_time_remaining > 0 else window_seconds
    time_span = max(1.0, window_upper - min_time_remaining)
    time_center = min_time_remaining + (time_span / 2)
    timing_score = 1.0 - _clamp(abs(remaining - time_center) / max(time_span / 2, 1.0), 0.0, 1.0)
    price_zone_score = 1.0 - _clamp(abs(market_yes_price - 0.5) / 0.38, 0.0, 1.0)
    momentum_score = _clamp(abs(m.get("momentum_pct", 0.0)) / max(min_momentum_pct * 2.0, 0.10), 0.0, 1.0)
    path_score = (
        0.40 * m.get("trend_ratio", 0.0)
        + 0.30 * m.get("close_location", 0.0)
        + 0.30 * momentum_score
    )
    strike_score = _clamp(
        abs(strike_distance_pct) / max(min_strike_distance_pct * 1.5, 0.10),
        0.0,
        1.0,
    ) if strike_distance_pct is not None else 0.0
    edge_score = _clamp(abs(model_edge) / 0.10, 0.0, 1.0)
    reversal_penalty = 0.12 * _clamp(m.get("reversal_pct", 0.0), 0.0, 1.0)

    score = (
        0.28 * path_score
        + 0.24 * price_zone_score
        + 0.18 * timing_score
        + 0.16 * strike_score
        + 0.14 * edge_score
        - reversal_penalty
    )
    return _clamp(score, 0.0, 1.0)


def norm_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
    k = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = k * (a1 + k * (a2 + k * (a3 + k * (a4 + k * a5))))
    n = 1.0 - math.exp(-0.5 * x * x) * poly / math.sqrt(2 * math.pi)
    return n if x >= 0 else 1.0 - n
