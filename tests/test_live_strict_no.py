from __future__ import annotations

from types import SimpleNamespace

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop import main as main_module


def strict_no_config(**overrides):
    payload = {
        "direct_live_strict_no_enabled": True,
        "direct_live_strict_no_max_daily_trades": 1,
        "direct_live_strict_no_amount_usd": 3.0,
        "direct_live_strict_no_min_signal_score": 0.45,
        "direct_live_strict_no_min_setup_score": 0.50,
        "direct_live_strict_no_min_trend_ratio": 0.75,
        "direct_live_strict_no_min_volume_ratio": 0.50,
        "direct_live_strict_no_max_entry_price": 0.55,
        "direct_live_strict_no_max_recent_move_pct": 0.0,
        "direct_live_strict_no_max_one_min_move_pct": 0.0,
        "direct_live_no_enabled": True,
        "direct_live_max_daily_no_trades": 1,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def strict_no_decision(**overrides):
    payload = {
        "side": "no",
        "entry_price": 0.52,
        "signal_score": 0.52,
        "setup_score": 0.61,
        "market": {"condition_id": "0xabc"},
        "momentum": {
            "trend_ratio": 0.75,
            "recent_move_pct": -0.01,
            "one_min_move_pct": -0.002,
            "volume_ratio": 0.75,
        },
    }
    payload.update(overrides)
    return payload


def test_strict_no_accepts_only_high_volume_negative_move():
    reason = main_module.live_strict_no_skip_reason(strict_no_config(), strict_no_decision())
    assert reason is None


def test_strict_no_rejects_thin_volume():
    decision = strict_no_decision(momentum={**strict_no_decision()["momentum"], "volume_ratio": 0.49})
    reason = main_module.live_strict_no_skip_reason(strict_no_config(), decision)
    assert reason is not None
    assert "volume" in reason


def test_strict_no_uses_separate_daily_cap(monkeypatch):
    monkeypatch.setattr(main_module, "count_live_successes_today", lambda side=None, market_flag=None: 1)
    reason = main_module.live_side_cap_skip_reason(strict_no_config(), strict_no_decision())
    assert reason == "daily strict NO limit reached (1/1)"


def test_strict_no_reduces_position_size_and_marks_bucket():
    config = strict_no_config()
    decision = SimpleNamespace(
        side="no",
        amount_usd=5.0,
        to_dict=lambda: {
            "side": decision.side,
            "amount_usd": decision.amount_usd,
            "market": {},
        },
    )
    decision_dict = main_module.apply_live_experiment_overrides(config, decision)
    assert decision.amount_usd == 3.0
    assert decision_dict["amount_usd"] == 3.0
    assert decision_dict["market"]["strict_no_micro_test"] is True
