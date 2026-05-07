from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Optional

from .clob_exec import OutcomeBooks
from .config import BotConfig
from .markets import FastMarket, WINDOW_SECONDS
from .signal import (
    Momentum,
    compute_setup_score,
    compute_signal_score,
    estimate_path_aware_yes_prob,
    get_binance_price_at,
    infer_move_side,
    momentum_to_dict,
    norm_cdf,
)


POLY_DEFAULT_CRYPTO_TAKER_FEE_RATE = 0.072


@dataclass
class Decision:
    should_trade: bool
    skip_reason: Optional[str]
    side: Optional[str]
    token_id: Optional[str]
    entry_price: Optional[float]
    order_type: str
    amount_usd: float
    limit_price: Optional[float]
    estimated_yes_prob: Optional[float]
    signal_score: Optional[float]
    setup_score: Optional[float]
    model_edge: Optional[float]
    divergence: Optional[float]
    fee_rate_bps: int
    fee_per_share: Optional[float]
    min_divergence: Optional[float]
    consensus_inputs: list[str]
    consensus_side: Optional[str]
    consensus_votes: Optional[int]
    rationale: Optional[str]
    market: dict
    momentum: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def polymarket_taker_fee_rate(fee_rate_bps=None) -> float:
    raw = _safe_float(fee_rate_bps)
    if raw is None or raw <= 0:
        return POLY_DEFAULT_CRYPTO_TAKER_FEE_RATE
    if raw > 1:
        return raw / 10000.0
    return raw


def polymarket_fee_per_share(price, fee_rate_bps=None) -> float:
    p = _safe_float(price)
    if p is None or p <= 0 or p >= 1:
        return 0.0
    rate = polymarket_taker_fee_rate(fee_rate_bps)
    return rate * p * (1 - p)


def polymarket_effective_fee_rate(price, fee_rate_bps=None) -> float:
    p = _safe_float(price)
    if p is None or p <= 0:
        return 0.0
    return polymarket_fee_per_share(p, fee_rate_bps) / p


def _floor_to_tick(price: float, tick_size: float) -> float:
    tick = max(float(tick_size or 0.01), 0.001)
    return round(math.floor((float(price) + 1e-12) / tick) * tick, 6)


def spread_is_tradeable(config: BotConfig, spread_pct: float, spread_cents: float, market_yes_price: float) -> bool:
    tail_price = min(market_yes_price, 1 - market_yes_price)
    if tail_price <= 0.10:
        return spread_cents <= config.max_spread_cents
    return spread_pct <= config.max_spread_pct


def compute_passive_maker_limit(
    config: BotConfig,
    side: str,
    current_ask: Optional[float],
    estimated_yes_prob: Optional[float],
    remaining_seconds: float,
) -> tuple[Optional[float], Optional[str], Optional[float]]:
    maker = config.maker
    if side != "yes":
        return None, "passive maker supports YES only", None
    if current_ask is None:
        return None, "passive maker missing current ask", None
    if estimated_yes_prob is None:
        return None, "passive maker missing model probability", None
    if remaining_seconds < maker.min_time_remaining:
        return None, "passive maker too close to expiry", None
    if current_ask > maker.max_current_ask:
        return None, "passive maker current ask too high", None

    raw_bid = min(
        current_ask - maker.bid_offset,
        maker.max_bid,
        estimated_yes_prob - maker.min_edge_to_bid,
    )
    bid = _floor_to_tick(raw_bid, 0.01)
    edge_to_bid = estimated_yes_prob - bid

    if bid < maker.min_bid:
        return None, "passive maker bid below minimum", edge_to_bid
    if bid >= current_ask:
        return None, "passive maker bid would cross the spread", edge_to_bid
    if bid <= 0 or bid >= 1:
        return None, "passive maker bid outside valid price range", edge_to_bid
    if edge_to_bid < maker.min_edge_to_bid:
        return None, "passive maker edge to bid too small", edge_to_bid

    return bid, None, edge_to_bid


def _window_seconds(config: BotConfig) -> int:
    return WINDOW_SECONDS.get(config.window, 300)


def _market_base(market: FastMarket, remaining: float, market_yes_price: Optional[float], books: OutcomeBooks) -> dict:
    no_price = 1 - market_yes_price if market_yes_price is not None else None
    return {
        "question": market.question,
        "slug": market.slug,
        "condition_id": market.condition_id,
        "end_time_utc": market.end_time.isoformat(),
        "remaining_seconds": round(remaining, 3),
        "yes_token_id": market.yes_token_id,
        "no_token_id": market.no_token_id,
        "market_yes_price": market_yes_price,
        "market_no_price": no_price,
        "yes_best_bid": books.yes.best_bid,
        "yes_best_ask": books.yes.best_ask,
        "yes_spread_cents": books.yes.spread_cents,
        "source": market.source,
    }


def evaluate_trade(
    config: BotConfig,
    market: FastMarket,
    books: OutcomeBooks,
    momentum: Momentum,
    fee_rate_bps: int,
    mode: str = "taker",
    market_yes_price: Optional[float] = None,
) -> Decision:
    """Decision engine ported from the current FastLoop bot.

    The only deliberate difference is execution plumbing: this returns a direct
    CLOB order candidate instead of calling Simmer's client.trade().
    """
    remaining = market.remaining_seconds()
    m = momentum_to_dict(momentum)
    if market_yes_price is None and books.yes.best_bid is not None and books.yes.best_ask is not None:
        market_yes_price = (books.yes.best_bid + books.yes.best_ask) / 2

    signal_score = compute_signal_score(m, min_momentum_pct=config.min_momentum_pct)
    base = _market_base(market, remaining, market_yes_price, books)

    estimated_yes_prob = None
    model_edge = None
    setup_score = None
    divergence = None
    fee_per_share = None
    min_divergence = None
    consensus_inputs: list[str] = []
    consensus_side = None
    consensus_votes = None
    rationale = None
    side = None
    required_model_edge = None

    def skip(reason: str, attempted: int = 0) -> Decision:
        return Decision(
            should_trade=False,
            skip_reason=reason,
            side=side,
            token_id=None,
            entry_price=None,
            order_type=config.order_type,
            amount_usd=config.max_position,
            limit_price=None,
            estimated_yes_prob=estimated_yes_prob,
            signal_score=signal_score,
            setup_score=setup_score,
            model_edge=model_edge,
            divergence=divergence,
            fee_rate_bps=fee_rate_bps,
            fee_per_share=fee_per_share,
            min_divergence=min_divergence,
            consensus_inputs=consensus_inputs,
            consensus_side=consensus_side,
            consensus_votes=consensus_votes,
            rationale=rationale or reason,
            market={**base, "trades_attempted": attempted},
            momentum=m,
        )

    if market_yes_price is None:
        return skip("clob price unavailable")

    direction = m["direction"]
    direction_sign = 1 if direction == "up" else -1
    momentum_pct = abs(m["momentum_pct"])
    start_time = market.end_time - timedelta(seconds=_window_seconds(config))
    market_open_price = get_binance_price_at(config.asset, start_time)
    strike_distance_pct = None
    strike_side = None
    if market_open_price and market_open_price > 0:
        strike_distance_pct = ((m["price_now"] - market_open_price) / market_open_price) * 100
        strike_side = "yes" if strike_distance_pct > 0 else "no"
        base["market_open_price"] = market_open_price
        base["strike_distance_pct"] = strike_distance_pct

    momentum_side = "yes" if direction == "up" else "no"
    micro_side = infer_move_side(m["two_min_move_pct"], config.min_confirmation_pct)
    if micro_side is None:
        micro_side = infer_move_side(m["one_min_move_pct"], config.min_confirmation_pct)
    estimated_yes_prob = estimate_path_aware_yes_prob(
        m,
        remaining,
        strike_distance_pct,
        min_momentum_pct=config.min_momentum_pct,
        min_confirmation_pct=config.min_confirmation_pct,
        min_strike_distance_pct=config.min_strike_distance_pct,
        min_time_remaining=config.min_time_remaining,
        max_time_remaining=config.max_time_remaining,
        window_seconds=_window_seconds(config),
    )
    model_edge = estimated_yes_prob - market_yes_price
    setup_score = compute_setup_score(
        m,
        remaining,
        market_yes_price,
        strike_distance_pct,
        model_edge,
        min_momentum_pct=config.min_momentum_pct,
        min_strike_distance_pct=config.min_strike_distance_pct,
        min_time_remaining=config.min_time_remaining,
        max_time_remaining=config.max_time_remaining,
        window_seconds=_window_seconds(config),
    )
    strike_distance_abs = abs(strike_distance_pct) if strike_distance_pct is not None else 0.0
    structure_ok = (
        m["trend_ratio"] >= max(config.min_trend_ratio, 0.55)
        and m["close_location"] >= config.min_close_location
    )
    strike_aligned = bool(strike_side) and strike_side == momentum_side
    strike_context_ok = (
        strike_aligned
        and strike_distance_abs >= config.min_strike_distance_pct
        and structure_ok
    )
    late_stage_override = (
        remaining <= config.late_stage_seconds
        and strike_distance_abs >= config.late_stage_buffer_pct
        and strike_aligned
    )
    base.update(
        {
            "structure_ok": structure_ok,
            "strike_context_ok": strike_context_ok,
            "late_stage_override": late_stage_override,
            "momentum_side": momentum_side,
            "micro_side": micro_side,
            "strike_side": strike_side,
        }
    )

    choppy_yes_exception = (
        bool(getattr(config, "direct_live_choppy_yes_enabled", False))
        and momentum_side == "yes"
        and m["trend_ratio"] >= float(getattr(config, "direct_live_choppy_yes_min_trend_ratio", 0.50))
        and m["trend_ratio"] < config.min_trend_ratio
        and signal_score >= float(getattr(config, "direct_live_choppy_yes_min_signal_score", 0.50))
        and setup_score >= float(getattr(config, "direct_live_choppy_yes_min_setup_score", 0.55))
    )
    if choppy_yes_exception:
        base["choppy_yes_exception"] = True

    if market_yes_price < config.min_contract_price or market_yes_price > config.max_contract_price:
        return skip("tail contract pricing")

    if books.yes.best_bid is not None and books.yes.best_ask is not None:
        spread_cents = (books.yes.best_ask - books.yes.best_bid) * 100
        mid = (books.yes.best_ask + books.yes.best_bid) / 2
        spread_pct = (books.yes.best_ask - books.yes.best_bid) / mid if mid > 0 else 0.0
        base["spread_cents"] = spread_cents
        base["spread_pct"] = spread_pct
        if not spread_is_tradeable(config, spread_pct, spread_cents, market_yes_price):
            return skip("wide spread")

    momentum_floor = 0.01 if config.use_fair_value else config.min_momentum_pct
    probe_trade = False
    if momentum_pct < momentum_floor:
        return skip("momentum too weak")

    if config.require_confirmation:
        one_min_aligned = direction_sign * m["one_min_move_pct"]
        two_min_aligned = direction_sign * m["two_min_move_pct"]
        short_term_reversal = (
            one_min_aligned < -config.min_confirmation_pct
            and two_min_aligned < -config.min_confirmation_pct
        )
        if short_term_reversal and not (strike_context_ok or late_stage_override):
            return skip("short-term reversal")

    if config.use_alpha_filter:
        if signal_score < config.min_signal_score:
            return skip("weak signal score")
        if m["trend_ratio"] < config.min_trend_ratio and not choppy_yes_exception:
            return skip("choppy move")
        if m["reversal_pct"] >= 0.45:
            return skip("late reversal risk")

    fair_yes = None
    if config.use_fair_value:
        btc_start_price = market_open_price
        if btc_start_price and btc_start_price > 0 and remaining > 30:
            log_ret = math.log(m["price_now"] / btc_start_price)
            sigma_tau = config.btc_annual_vol * math.sqrt(remaining / 31_536_000)
            d_value = log_ret / sigma_tau if sigma_tau > 0 else 0
            fair_yes = norm_cdf(d_value)
            edge = fair_yes - market_yes_price
            if abs(edge) < config.fair_value_min_edge:
                return skip("insufficient edge")
            side = "yes" if edge > 0 else "no"
            divergence = abs(edge)
            rationale = f"fair YES={fair_yes:.3f} vs market={market_yes_price:.3f} ({edge:+.3f} edge, d={d_value:.2f})"
            if side != momentum_side:
                if not config.allow_contrarian_fair_value:
                    return skip("contrarian fair-value setup")
                if divergence < config.contrarian_min_edge:
                    return skip("contrarian edge too small")
        else:
            if direction == "up":
                side = "yes"
                divergence = 0.50 + config.entry_threshold - market_yes_price
            else:
                side = "no"
                divergence = market_yes_price - (0.50 - config.entry_threshold)
            rationale = f"momentum fallback: {config.asset} {m['momentum_pct']:+.3f}%"
            if divergence <= 0:
                return skip("market already priced in")
    else:
        if config.strategy_mode == "momentum_first":
            aligned_edge = model_edge if momentum_side == "yes" else -model_edge
            entry_price = market_yes_price if momentum_side == "yes" else (1 - market_yes_price)
            entry_price_cap = config.max_entry_price_yes if momentum_side == "yes" else config.max_entry_price_no

            if (
                momentum_side == "yes"
                and strike_distance_pct is not None
                and strike_distance_abs <= 0.01
            ):
                entry_price_cap = max(entry_price_cap, config.max_entry_price_yes_premium)

            if config.allowed_side == "yes_only" and momentum_side != "yes":
                side = momentum_side
                return skip("side disabled")
            if config.allowed_side == "no_only" and momentum_side != "no":
                side = momentum_side
                return skip("side disabled")
            if m["trend_ratio"] < config.min_trend_ratio and not choppy_yes_exception:
                return skip("trend too weak")
            if m["close_location"] < config.min_close_location:
                return skip("close location too weak")
            if strike_distance_pct is not None and abs(strike_distance_pct) > config.max_strike_distance_pct:
                return skip("move already extended")
            if entry_price > entry_price_cap:
                return skip("entry price too high")
            if aligned_edge < config.min_aligned_edge:
                return skip("model confirmation too weak")

            model_edge = abs(aligned_edge) if momentum_side == "yes" else -abs(aligned_edge)
            micro_side = momentum_side
            strike_side = momentum_side
            strike_distance_abs = max(strike_distance_abs, config.min_strike_distance_pct)

        side = "yes" if model_edge > 0 else "no"
        required_setup_score = config.min_setup_score_yes if side == "yes" else config.min_setup_score_no
        required_model_edge = config.min_model_edge_yes if side == "yes" else config.min_model_edge_no
        divergence = abs(model_edge)
        consensus_inputs = [momentum_side]
        if strike_side and strike_distance_abs >= config.min_strike_distance_pct:
            consensus_inputs.append(strike_side)
        if micro_side:
            consensus_inputs.append(micro_side)
        yes_votes = sum(1 for vote in consensus_inputs if vote == "yes")
        no_votes = sum(1 for vote in consensus_inputs if vote == "no")
        consensus_votes = max(yes_votes, no_votes)
        if yes_votes > no_votes:
            consensus_side = "yes"
        elif no_votes > yes_votes:
            consensus_side = "no"

        if setup_score < required_setup_score:
            return skip("setup too weak")
        if divergence < required_model_edge:
            return skip("model edge too small")
        if config.require_signal_consensus:
            if consensus_side is None or consensus_votes < 2:
                return skip("signal consensus too weak")
            if side != consensus_side:
                return skip("consensus mismatch")

        if strike_distance_pct is not None:
            rationale = (
                f"path-aware {side.upper()}: model YES {estimated_yes_prob:.3f} vs market {market_yes_price:.3f} "
                f"(strike {strike_distance_pct:+.3f}% | trend {m['trend_ratio']:.2f} | "
                f"close {m['close_location']:.2f})"
            )
        else:
            rationale = (
                f"path-aware {side.upper()}: model YES {estimated_yes_prob:.3f} vs market {market_yes_price:.3f} "
                f"(trend {m['trend_ratio']:.2f} | close {m['close_location']:.2f})"
            )
        if config.strategy_mode == "momentum_first":
            if strike_distance_pct is not None:
                rationale = (
                    f"momentum-first {side.upper()}: momentum {m['momentum_pct']:+.3f}% | "
                    f"model YES {estimated_yes_prob:.3f} vs market {market_yes_price:.3f} "
                    f"(strike {strike_distance_pct:+.3f}% | trend {m['trend_ratio']:.2f} | "
                    f"close {m['close_location']:.2f})"
                )
            else:
                rationale = (
                    f"momentum-first {side.upper()}: momentum {m['momentum_pct']:+.3f}% | "
                    f"model YES {estimated_yes_prob:.3f} vs market {market_yes_price:.3f} "
                    f"(trend {m['trend_ratio']:.2f} | close {m['close_location']:.2f})"
                )

    if divergence is None or divergence <= 0:
        return skip("market already priced in")
    if side == "yes" and setup_score is not None and setup_score < config.min_setup_score_yes_trade:
        return skip("yes setup too weak")
    if config.use_empirical_guard:
        return skip("empirical guard unsupported in direct parity")

    buy_price = market_yes_price if side == "yes" else (1 - market_yes_price)
    fee_per_share = polymarket_fee_per_share(buy_price, fee_rate_bps)
    edge_floor = required_model_edge if required_model_edge is not None else config.min_model_edge
    min_divergence = edge_floor + fee_per_share
    if divergence < min_divergence:
        return skip("fees eat the edge")

    amount = float(config.max_position)
    order_type = config.order_type
    limit_price = None
    execution_price = buy_price
    maker_edge_to_bid = None
    if mode == "maker" or config.maker.enabled:
        maker_limit_price, maker_skip, maker_edge_to_bid = compute_passive_maker_limit(
            config=config,
            side=side,
            current_ask=market_yes_price,
            estimated_yes_prob=estimated_yes_prob,
            remaining_seconds=remaining,
        )
        if maker_skip:
            return skip(maker_skip)
        amount = min(amount, config.maker.max_order_usd)
        order_type = "GTC"
        limit_price = maker_limit_price
        execution_price = maker_limit_price
        base["maker_edge_to_bid"] = maker_edge_to_bid

    if execution_price and config.min_shares_per_order * execution_price > amount:
        return skip("position too small", attempted=1)

    token_id = market.yes_token_id if side == "yes" else market.no_token_id
    if not token_id:
        return skip(f"{side} token unavailable")

    return Decision(
        should_trade=True,
        skip_reason=None,
        side=side,
        token_id=token_id,
        entry_price=round(float(buy_price), 6),
        order_type=order_type,
        amount_usd=round(amount, 2),
        limit_price=limit_price,
        estimated_yes_prob=estimated_yes_prob,
        signal_score=signal_score,
        setup_score=setup_score,
        model_edge=model_edge,
        divergence=divergence,
        fee_rate_bps=fee_rate_bps,
        fee_per_share=fee_per_share,
        min_divergence=min_divergence,
        consensus_inputs=consensus_inputs,
        consensus_side=consensus_side,
        consensus_votes=consensus_votes,
        rationale=rationale,
        market=base,
        momentum=m,
    )
