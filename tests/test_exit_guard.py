from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop.config import load_config
from direct_fastloop.main import build_exit_decision


def fake_market(remaining: float = 82.0):
    return SimpleNamespace(
        question="Bitcoin Up or Down - test",
        slug="btc-updown-5m-test",
        condition_id="0xtest",
        yes_token_id="yes-token",
        no_token_id="no-token",
        end_time=datetime.now(timezone.utc),
        remaining_seconds=lambda: remaining,
    )


def fake_books(bid: float = 0.48, ask: float = 0.49):
    return SimpleNamespace(
        yes=SimpleNamespace(best_bid=bid, best_ask=ask),
        no=SimpleNamespace(best_bid=1.0 - ask, best_ask=1.0 - bid),
    )


def fake_order():
    return {
        "timestamp_utc": "2026-05-07T03:33:05+00:00",
        "live": True,
        "decision": {"side": "yes", "entry_price": 0.625},
        "result": {"success": True, "takingAmount": "7.692305", "makingAmount": "4.999998"},
    }


def current_signal(skip_reason: str | None, signal: float, close_location: float, momentum_side: str = "yes"):
    return {
        "skip_reason": skip_reason,
        "signal_score": signal,
        "market": {"momentum_side": momentum_side, "micro_side": "yes"},
        "momentum": {
            "close_location": close_location,
            "trend_ratio": 0.75,
            "recent_move_pct": 0.03,
            "one_min_move_pct": 0.01,
        },
    }


def test_exit_triggers_on_loss_and_confluence():
    config = load_config()
    decision = build_exit_decision(
        config,
        fake_market(remaining=82),
        fake_books(bid=0.48),
        fake_order(),
        current_signal("close location too weak", signal=0.52, close_location=0.74),
        fee_rate_bps=1000,
    )
    assert decision["should_exit"] is True
    assert decision["limit_price"] == 0.45
    assert len(decision["bad_reasons"]) >= 2


def test_exit_does_not_trigger_too_early():
    config = load_config()
    decision = build_exit_decision(
        config,
        fake_market(remaining=115),
        fake_books(bid=0.27),
        fake_order(),
        current_signal("weak signal score", signal=0.25, close_location=0.60),
        fee_rate_bps=1000,
    )
    assert decision["should_exit"] is False
    assert "too early" in decision["skip_reason"]


def test_exit_requires_confluence():
    config = load_config()
    decision = build_exit_decision(
        config,
        fake_market(remaining=82),
        fake_books(bid=0.48),
        fake_order(),
        current_signal(None, signal=0.52, close_location=0.74),
        fee_rate_bps=1000,
    )
    assert decision["should_exit"] is False
    assert "confluence" in decision["skip_reason"]


def test_exit_does_not_trigger_when_loss_is_small():
    config = load_config()
    decision = build_exit_decision(
        config,
        fake_market(remaining=82),
        fake_books(bid=0.62),
        fake_order(),
        current_signal("weak signal score", signal=0.25, close_location=0.60),
        fee_rate_bps=1000,
    )
    assert decision["should_exit"] is False
    assert "loss not large enough" in decision["skip_reason"]

