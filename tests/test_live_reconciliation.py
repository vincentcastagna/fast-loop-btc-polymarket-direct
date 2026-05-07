from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop import main as main_module


def test_reconciles_ambiguous_request_error_from_activity(monkeypatch):
    decision = {
        "side": "yes",
        "token_id": "yes-token",
        "amount_usd": 5.0,
        "market": {
            "condition_id": "0xabc",
            "slug": "btc-updown-5m-1778170200",
            "question": "Bitcoin Up or Down - test",
        },
    }
    rows = [
        {
            "timestamp": 1778170436,
            "type": "TRADE",
            "side": "BUY",
            "outcome": "Up",
            "asset": "yes-token",
            "conditionId": "0xabc",
            "eventSlug": "btc-updown-5m-1778170200",
            "title": "Bitcoin Up or Down - test",
            "size": 15.625,
            "usdcSize": 5.238,
        }
    ]

    monkeypatch.setattr(main_module, "api_get_json", lambda *args, **kwargs: rows)

    result = main_module.reconcile_order_result_from_activity(
        "0xwallet",
        decision,
        {"success": False, "exception_type": "PolyApiException", "error": "Request exception!", "attempt": 1},
        datetime.fromtimestamp(1778170430, timezone.utc),
        wait_seconds=0,
    )

    assert result["success"] is True
    assert result["status"] == "matched"
    assert result["reconciled_from_activity"] is True
    assert result["makingAmount"] == "5.238"
    assert main_module.filled_cash_amount(result, 5.0) == 5.238


def test_unresolved_ambiguous_error_is_not_success(monkeypatch):
    decision = {
        "side": "yes",
        "token_id": "yes-token",
        "market": {"condition_id": "0xabc", "slug": "btc-updown-5m-1778170200"},
    }
    monkeypatch.setattr(main_module, "api_get_json", lambda *args, **kwargs: [])

    result = main_module.reconcile_order_result_from_activity(
        "0xwallet",
        decision,
        {"success": False, "exception_type": "PolyApiException", "error": "Request exception!", "attempt": 1},
        datetime.fromtimestamp(1778170430, timezone.utc),
        wait_seconds=0,
    )

    assert main_module.order_success(result) is False
    assert main_module.unresolved_ambiguous_order(result) is True


def test_exit_guard_scan_recovers_previous_ambiguous_fill(monkeypatch):
    decision = {
        "side": "yes",
        "token_id": "yes-token",
        "amount_usd": 5.0,
        "market": {
            "condition_id": "0xabc",
            "slug": "btc-updown-5m-1778170200",
            "question": "Bitcoin Up or Down - test",
        },
    }
    failed_record = {
        "timestamp_utc": "2026-05-07T16:13:51+00:00",
        "live": True,
        "decision": decision,
        "result": {
            "success": False,
            "exception_type": "PolyApiException",
            "error": "Request exception!",
            "attempt": 1,
        },
    }
    rows = [
        {
            "timestamp": 1778170436,
            "type": "TRADE",
            "side": "BUY",
            "outcome": "Up",
            "asset": "yes-token",
            "conditionId": "0xabc",
            "eventSlug": "btc-updown-5m-1778170200",
            "title": "Bitcoin Up or Down - test",
            "size": 9.259258,
            "usdcSize": 4.999999,
        }
    ]
    attempts = []

    monkeypatch.setattr(main_module, "get_failed_live_entry_for_market_today", lambda condition_id: failed_record)
    monkeypatch.setattr(main_module, "api_get_json", lambda *args, **kwargs: rows)
    monkeypatch.setattr(main_module, "record_attempt", lambda decision, result, live: attempts.append((decision, result, live)))

    recovered = main_module.recover_ambiguous_live_entry_from_activity("0xwallet", "0xabc")

    assert recovered is not None
    assert recovered["result"]["success"] is True
    assert recovered["result"]["reconciliation"]["source"] == "exit_guard_scan"
    assert recovered["result"]["makingAmount"] == "4.999999"
    assert len(attempts) == 1
    assert attempts[0][2] is True


def test_exit_guard_scan_does_not_log_without_activity_fill(monkeypatch):
    failed_record = {
        "timestamp_utc": "2026-05-07T16:13:51+00:00",
        "live": True,
        "decision": {"side": "yes", "token_id": "yes-token", "market": {"condition_id": "0xabc"}},
        "result": {
            "success": False,
            "exception_type": "PolyApiException",
            "error": "Request exception!",
            "attempt": 1,
        },
    }
    attempts = []

    monkeypatch.setattr(main_module, "get_failed_live_entry_for_market_today", lambda condition_id: failed_record)
    monkeypatch.setattr(main_module, "api_get_json", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "record_attempt", lambda decision, result, live: attempts.append((decision, result, live)))

    recovered = main_module.recover_ambiguous_live_entry_from_activity("0xwallet", "0xabc")

    assert recovered is None
    assert attempts == []
