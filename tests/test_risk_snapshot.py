from __future__ import annotations

from types import SimpleNamespace

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop import risk


def test_daily_cash_snapshot_counts_exit_sells(monkeypatch):
    config = SimpleNamespace(
        asset="BTC",
        window="5m",
        direct_guard_reset_utc="2026-05-08T00:00:00Z",
    )
    rows = [
        {
            "timestamp": 1778247460,
            "type": "TRADE",
            "side": "BUY",
            "title": "Bitcoin Up or Down - test",
            "eventSlug": "btc-updown-5m-1778247300",
            "usdcSize": 5.126,
        },
        {
            "timestamp": 1778247581,
            "type": "TRADE",
            "side": "SELL",
            "title": "Bitcoin Up or Down - test",
            "eventSlug": "btc-updown-5m-1778247300",
            "usdcSize": 1.39977,
        },
    ]

    monkeypatch.setattr(risk, "api_get_json", lambda *args, **kwargs: rows)

    snapshot = risk._daily_asset_cash_snapshot(config, "0xwallet")

    assert snapshot["cash_out"] == 5.126
    assert snapshot["cash_in"] == 1.39977
    assert snapshot["cash_pnl"] == -3.72623
    assert snapshot["losses"] == 1


def risk_config(**overrides):
    payload = {
        "direct_guard_reset_utc": None,
        "direct_max_daily_losses": 0,
        "direct_daily_stop_loss": -10.0,
        "direct_max_daily_trades": 10,
        "direct_daily_budget": 50.0,
        "direct_daily_profit_lock_enabled": True,
        "direct_daily_profit_lock_start": 5.0,
        "direct_daily_profit_lock_giveback": 3.5,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_profit_lock_blocks_after_intraday_giveback(monkeypatch):
    state = {"date": "2026-05-08", "spent": 10.0, "trades": 2, "peak_cash_pnl": 8.0}

    monkeypatch.setattr(risk, "load_daily_state", lambda: dict(state))
    monkeypatch.setattr(risk, "save_daily_state", lambda next_state: state.update(next_state))
    monkeypatch.setattr(
        risk,
        "_daily_asset_cash_snapshot",
        lambda config, user_address: {"cash_out": 10.0, "trade_count": 2, "cash_pnl": 4.0, "losses": 0},
    )

    result = risk.check_and_size(risk_config(), 5.0, user_address="0xwallet")

    assert result.ok is False
    assert "profit lock" in result.reason
    assert state["profit_lock_triggered"] is True


def test_profit_lock_tracks_new_peak_without_blocking(monkeypatch):
    state = {"date": "2026-05-08", "spent": 5.0, "trades": 1}

    monkeypatch.setattr(risk, "load_daily_state", lambda: dict(state))
    monkeypatch.setattr(risk, "save_daily_state", lambda next_state: state.update(next_state))
    monkeypatch.setattr(
        risk,
        "_daily_asset_cash_snapshot",
        lambda config, user_address: {"cash_out": 5.0, "trade_count": 1, "cash_pnl": 6.0, "losses": 0},
    )

    result = risk.check_and_size(risk_config(), 5.0, user_address="0xwallet")

    assert result.ok is True
    assert state["peak_cash_pnl"] == 6.0
    assert not state.get("profit_lock_triggered")
