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
