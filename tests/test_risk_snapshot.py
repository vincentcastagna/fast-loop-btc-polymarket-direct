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
