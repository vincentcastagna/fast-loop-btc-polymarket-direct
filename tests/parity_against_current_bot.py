from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop.config import load_config
from direct_fastloop.signal import (
    compute_setup_score,
    compute_signal_score,
    estimate_path_aware_yes_prob,
)
from direct_fastloop.strategy import (
    compute_passive_maker_limit,
    polymarket_fee_per_share,
    polymarket_taker_fee_rate,
)


ORIGINAL = Path(r"C:\Dev\TradeBtc\polymarket-fast-loop\fastloop_trader.py")


def load_original():
    spec = importlib.util.spec_from_file_location("original_fastloop_trader", ORIGINAL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {ORIGINAL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def almost_equal(left: float, right: float, tol: float = 1e-12) -> None:
    if abs(left - right) > tol:
        raise AssertionError(f"{left!r} != {right!r}")


def main() -> int:
    original = load_original()
    config = load_config()
    parity_min_time = getattr(original, "MIN_TIME_REMAINING", config.min_time_remaining)
    parity_max_time = getattr(original, "MAX_TIME_REMAINING", config.max_time_remaining)

    fixtures = [
        {
            "momentum_pct": 0.0712,
            "direction": "up",
            "price_now": 65012.5,
            "price_then": 64966.2,
            "avg_volume": 30.0,
            "latest_volume": 41.5,
            "volume_ratio": 1.3833333333,
            "trend_ratio": 0.80,
            "one_min_move_pct": 0.026,
            "two_min_move_pct": 0.051,
            "recent_move_pct": 0.051,
            "last_candle_move_pct": 0.026,
            "close_location": 0.91,
            "reversal_pct": 0.0,
            "candles": 5,
        },
        {
            "momentum_pct": -0.064,
            "direction": "down",
            "price_now": 64910.0,
            "price_then": 64951.5,
            "avg_volume": 27.0,
            "latest_volume": 22.0,
            "volume_ratio": 0.8148148148,
            "trend_ratio": 0.75,
            "one_min_move_pct": -0.019,
            "two_min_move_pct": -0.047,
            "recent_move_pct": -0.047,
            "last_candle_move_pct": -0.019,
            "close_location": 0.86,
            "reversal_pct": 0.0,
            "candles": 5,
        },
        {
            "momentum_pct": 0.041,
            "direction": "up",
            "price_now": 65010.0,
            "price_then": 64983.4,
            "avg_volume": 28.0,
            "latest_volume": 14.0,
            "volume_ratio": 0.5,
            "trend_ratio": 0.60,
            "one_min_move_pct": -0.022,
            "two_min_move_pct": 0.031,
            "recent_move_pct": 0.031,
            "last_candle_move_pct": -0.022,
            "close_location": 0.70,
            "reversal_pct": 0.5365853659,
            "candles": 5,
        },
    ]

    for momentum in fixtures:
        almost_equal(
            compute_signal_score(momentum, min_momentum_pct=config.min_momentum_pct),
            original.compute_signal_score(momentum),
        )
        for remaining, strike_distance in ((90, 0.023), (132, -0.041), (48, None)):
            almost_equal(
                estimate_path_aware_yes_prob(
                    momentum,
                    remaining,
                    strike_distance,
                    min_momentum_pct=config.min_momentum_pct,
                    min_confirmation_pct=config.min_confirmation_pct,
                    min_strike_distance_pct=config.min_strike_distance_pct,
                    min_time_remaining=parity_min_time,
                    max_time_remaining=parity_max_time,
                    window_seconds=300,
                ),
                original.estimate_path_aware_yes_prob(momentum, remaining, strike_distance),
            )
            edge = 0.081 if momentum["direction"] == "up" else -0.074
            almost_equal(
                compute_setup_score(
                    momentum,
                    remaining,
                    0.54,
                    strike_distance,
                    edge,
                    min_momentum_pct=config.min_momentum_pct,
                    min_strike_distance_pct=config.min_strike_distance_pct,
                    min_time_remaining=parity_min_time,
                    max_time_remaining=parity_max_time,
                    window_seconds=300,
                ),
                original.compute_setup_score(momentum, remaining, 0.54, strike_distance, edge),
            )

    for fee_rate in (0, 72, 720, 1000, 0.072):
        almost_equal(polymarket_taker_fee_rate(fee_rate), original.polymarket_taker_fee_rate(fee_rate))
        almost_equal(polymarket_fee_per_share(0.54, fee_rate), original.polymarket_fee_per_share(0.54, fee_rate))

    almost_equal(
        compute_passive_maker_limit(config, "yes", 0.62, 0.79, 100)[0],
        original.compute_passive_maker_limit("yes", 0.62, 0.79, 100)[0],
    )

    print("parity ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
