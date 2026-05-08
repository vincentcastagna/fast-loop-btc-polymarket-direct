from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop import ledger
from direct_fastloop import main as main_module


def confirmation_config(**overrides):
    payload = {
        "direct_live_entry_confirmation_enabled": True,
        "direct_live_entry_confirmation_min_wait_seconds": 25,
        "direct_live_entry_confirmation_min_remaining_seconds": 95,
        "direct_live_entry_confirmation_max_age_seconds": 130,
        "direct_live_entry_confirmation_max_price_slippage_cents": 3.0,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def decision(**overrides):
    payload = {
        "side": "yes",
        "entry_price": 0.52,
        "signal_score": 0.56,
        "setup_score": 0.62,
        "market": {
            "condition_id": "0xabc",
            "slug": "btc-updown-5m-1778247300",
            "question": "Bitcoin Up or Down - test",
            "remaining_seconds": 120,
        },
    }
    payload.update(overrides)
    return payload


def test_entry_confirmation_requires_second_stable_sighting(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "ENTRY_CONFIRM_STATE", tmp_path / "entry_confirm.json")
    now = datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc)

    first = decision()
    first_reason = main_module.live_entry_confirmation_skip_reason(confirmation_config(), first, now=now)
    assert first_reason == "entry confirmation pending (first sighting)"
    assert first["market"]["entry_confirmation"]["status"] == "pending"

    second = decision(entry_price=0.53)
    second_reason = main_module.live_entry_confirmation_skip_reason(
        confirmation_config(),
        second,
        now=now + timedelta(seconds=30),
    )
    assert second_reason is None
    assert second["market"]["entry_confirmation"]["status"] == "confirmed"
    assert second["market"]["entry_confirmation"]["seen_count"] == 2


def test_entry_confirmation_ignores_late_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "ENTRY_CONFIRM_STATE", tmp_path / "entry_confirm.json")
    now = datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc)

    reason = main_module.live_entry_confirmation_skip_reason(
        confirmation_config(),
        decision(market={**decision()["market"], "remaining_seconds": 70}),
        now=now,
    )

    assert reason is None
    assert not ledger.ENTRY_CONFIRM_STATE.exists()


def test_entry_confirmation_resets_when_price_slips(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "ENTRY_CONFIRM_STATE", tmp_path / "entry_confirm.json")
    now = datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc)

    main_module.live_entry_confirmation_skip_reason(confirmation_config(), decision(entry_price=0.50), now=now)
    slipped = decision(entry_price=0.55)
    reason = main_module.live_entry_confirmation_skip_reason(
        confirmation_config(),
        slipped,
        now=now + timedelta(seconds=30),
    )

    assert reason == "entry confirmation price slipped (0.500->0.550)"
    assert slipped["market"]["entry_confirmation"]["status"] == "price_reset"


def test_active_confirmation_does_not_bypass_below_min_remaining(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "ENTRY_CONFIRM_STATE", tmp_path / "entry_confirm.json")
    now = datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc)

    main_module.live_entry_confirmation_skip_reason(
        confirmation_config(),
        decision(market={**decision()["market"], "remaining_seconds": 105}),
        now=now,
    )
    still_pending = decision(market={**decision()["market"], "remaining_seconds": 85})
    reason = main_module.live_entry_confirmation_skip_reason(
        confirmation_config(),
        still_pending,
        now=now + timedelta(seconds=10),
    )

    assert reason == "entry confirmation pending (10/25s)"
    assert still_pending["market"]["entry_confirmation"]["status"] == "pending"
