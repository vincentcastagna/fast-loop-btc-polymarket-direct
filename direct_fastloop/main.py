from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

from py_clob_client_v2.clob_types import OrderType

from .clob_exec import DirectClob
from .config import load_config, load_dotenv, load_wallet_config
from .ledger import (
    count_live_successes_today,
    get_live_success_for_market_today,
    has_live_success_for_market_today,
    record_attempt,
    record_decision,
    record_shadow,
)
from .markets import choose_live_market, discover_fast_markets
from .risk import check_and_size, mark_live_success
from .signal import get_binance_momentum
from .strategy import evaluate_trade


def _json_default(value: Any) -> str:
    return str(value)


def print_json(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj, indent=2, default=_json_default))


def normalize_order_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    return {"repr": repr(result)}


def normalize_order_exception(exc: Exception) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": False,
        "exception_type": type(exc).__name__,
        "error": str(exc),
    }
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        payload["status_code"] = status_code
    error_message = getattr(exc, "error_message", None)
    if error_message is not None:
        payload["error_message"] = error_message
    return payload


def order_success(result: Dict[str, Any]) -> bool:
    if result.get("success") is True:
        return True
    if result.get("success") is False or result.get("error") or result.get("exception_type"):
        return False
    status = str(result.get("status") or "").lower()
    if status in {"matched", "live"}:
        return True
    return bool(result.get("orderID") or result.get("order_id") or result.get("id"))


def retryable_taker_error(result: Dict[str, Any]) -> bool:
    text = json.dumps(result, default=_json_default).lower()
    return any(
        needle in text
        for needle in (
            "no orders found to match",
            "service not ready",
            "read operation timed out",
            "request exception",
            "sslwantreaderror",
        )
    )


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def live_quality_skip_reason(config, decision: Dict[str, Any]) -> str | None:
    if not config.direct_live_quality_filter:
        return None

    side = decision.get("side")
    market = decision.get("market") or {}
    momentum = decision.get("momentum") or {}

    if config.direct_live_require_micro_side and market.get("micro_side") != side:
        return f"live quality guard: micro side not confirmed ({market.get('micro_side')})"

    recent_move = _float_or_none(momentum.get("recent_move_pct"))
    one_min_move = _float_or_none(momentum.get("one_min_move_pct"))
    volume_ratio = _float_or_none(momentum.get("volume_ratio"))

    min_recent = float(config.direct_live_min_recent_move_pct)
    if side == "yes":
        if recent_move is None or recent_move < min_recent:
            return f"live quality guard: recent YES move too weak ({recent_move})"
        if config.direct_live_block_negative_one_min and (one_min_move is None or one_min_move < 0):
            return f"live quality guard: one-minute YES move negative ({one_min_move})"
    elif side == "no":
        if recent_move is None or recent_move > -min_recent:
            return f"live quality guard: recent NO move too weak ({recent_move})"
        if config.direct_live_block_negative_one_min and (one_min_move is None or one_min_move > 0):
            return f"live quality guard: one-minute NO move positive ({one_min_move})"

    min_volume = float(config.direct_live_min_volume_ratio)
    if volume_ratio is None or volume_ratio < min_volume:
        return f"live quality guard: volume too thin ({volume_ratio})"

    return None


def live_side_cap_skip_reason(config, decision: Dict[str, Any]) -> str | None:
    if decision.get("side") != "no":
        return None
    if not config.direct_live_no_enabled:
        return "live NO micro-test disabled"
    max_no_trades = int(config.direct_live_max_daily_no_trades or 0)
    if max_no_trades <= 0:
        return "live NO daily limit disabled"
    no_trades_today = count_live_successes_today("no")
    if no_trades_today >= max_no_trades:
        return f"daily NO limit reached ({no_trades_today}/{max_no_trades})"
    return None


def live_choppy_yes_cap_skip_reason(config, decision: Dict[str, Any]) -> str | None:
    market = decision.get("market") or {}
    if decision.get("side") != "yes" or not market.get("choppy_yes_exception"):
        return None
    if not config.direct_live_choppy_yes_enabled:
        return "live choppy YES micro-test disabled"
    max_choppy = int(config.direct_live_max_daily_choppy_yes_trades or 0)
    if max_choppy <= 0:
        return "live choppy YES daily limit disabled"
    choppy_trades_today = count_live_successes_today("yes", market_flag="choppy_yes_exception")
    if choppy_trades_today >= max_choppy:
        return f"daily choppy YES limit reached ({choppy_trades_today}/{max_choppy})"
    return None


def live_experiment_cap_skip_reason(config, decision: Dict[str, Any]) -> str | None:
    return live_side_cap_skip_reason(config, decision) or live_choppy_yes_cap_skip_reason(config, decision)


def _order_execution_snapshot(order_record: Dict[str, Any]) -> Dict[str, Any]:
    decision = order_record.get("decision") or {}
    result = order_record.get("result") or {}
    shares = _float_or_none(result.get("takingAmount"))
    cost = _float_or_none(result.get("makingAmount"))
    avg_price = None
    if shares and shares > 0 and cost is not None:
        avg_price = cost / shares
    return {
        "timestamp_utc": order_record.get("timestamp_utc"),
        "side": decision.get("side"),
        "entry_price": decision.get("entry_price"),
        "actual_cost": cost,
        "actual_shares": shares,
        "actual_avg_price": avg_price,
        "order_id": result.get("orderID") or result.get("order_id") or result.get("id"),
    }


def _record_shadow_exit_snapshot(config, market, books, live: bool) -> None:
    if not config.direct_shadow_exit_enabled:
        return
    order_record = get_live_success_for_market_today(market.condition_id)
    if not order_record:
        return
    execution = _order_execution_snapshot(order_record)
    side = execution.get("side")
    if side == "yes":
        current_bid = books.yes.best_bid
        current_ask = books.yes.best_ask
    elif side == "no" and books.no:
        current_bid = books.no.best_bid
        current_ask = books.no.best_ask
    else:
        return

    shares = execution.get("actual_shares")
    cost = execution.get("actual_cost")
    exit_value = None
    exit_pnl = None
    if shares is not None and cost is not None and current_bid is not None:
        exit_value = float(shares) * float(current_bid)
        exit_pnl = exit_value - float(cost)

    record_shadow(
        {
            "shadow_type": "exit_snapshot",
            "market": {
                "question": market.question,
                "slug": market.slug,
                "condition_id": market.condition_id,
                "end_time_utc": market.end_time.isoformat(),
                "remaining_seconds": round(market.remaining_seconds(), 3),
            },
            "position": execution,
            "current_bid": current_bid,
            "current_ask": current_ask,
            "exit_value_at_bid": exit_value,
            "exit_pnl_at_bid": exit_pnl,
        },
        live=live,
        status="shadow_exit_snapshot",
    )


def record_late_shadow_observations(config, wallet, clob: DirectClob, markets, mode: str, live: bool) -> None:
    if not config.direct_shadow_late_enabled or mode != "taker":
        return
    market = choose_live_market(
        markets,
        config.direct_shadow_late_min_time_remaining,
        config.direct_shadow_late_max_time_remaining,
        config.window,
    )
    if not market:
        return

    try:
        books = clob.get_outcome_books(market.yes_token_id, market.no_token_id)
    except Exception as exc:
        record_shadow(
            {
                "shadow_type": "late_entry",
                "skip_reason": f"shadow orderbook unavailable: {exc}",
                "market": {"question": market.question, "condition_id": market.condition_id},
            },
            live=live,
            status="shadow_late_skipped",
        )
        return

    _record_shadow_exit_snapshot(config, market, books, live=live)

    market_yes_price = clob.get_midpoint(market.yes_token_id)
    momentum = get_binance_momentum(config.asset, config.lookback_minutes)
    if not momentum:
        record_shadow(
            {
                "shadow_type": "late_entry",
                "skip_reason": "shadow signal fetch failed",
                "market": {"question": market.question, "condition_id": market.condition_id},
            },
            live=live,
            status="shadow_late_skipped",
        )
        return

    fee_rate = clob.get_fee_rate_bps(market.yes_token_id, config.default_taker_fee_rate_bps)
    decision = evaluate_trade(
        config,
        market,
        books,
        momentum,
        fee_rate_bps=fee_rate,
        mode=mode,
        market_yes_price=market_yes_price,
    )
    decision_dict = decision.to_dict()
    decision_dict["shadow_type"] = "late_entry"
    decision_dict["shadow_window"] = {
        "min_time_remaining": config.direct_shadow_late_min_time_remaining,
        "max_time_remaining": config.direct_shadow_late_max_time_remaining,
    }

    status = "shadow_late_candidate" if decision.should_trade else "shadow_late_skipped"
    if decision.should_trade:
        condition_id = (decision_dict.get("market") or {}).get("condition_id")
        if condition_id and has_live_success_for_market_today(condition_id):
            decision_dict["should_trade"] = False
            decision_dict["skip_reason"] = "shadow already traded market live today"
            status = "shadow_late_skipped"

        quality_skip = live_quality_skip_reason(config, decision_dict)
        if quality_skip:
            decision_dict["should_trade"] = False
            decision_dict["skip_reason"] = quality_skip
            status = "shadow_late_skipped"

        experiment_skip = live_experiment_cap_skip_reason(config, decision_dict)
        if experiment_skip:
            decision_dict["should_trade"] = False
            decision_dict["skip_reason"] = experiment_skip
            status = "shadow_late_skipped"

    record_shadow(decision_dict, live=live, status=status)


def taker_price_limit(decision, books) -> float:
    if decision.order_type not in (OrderType.FAK, OrderType.FOK):
        return 0.0
    if decision.side == "yes":
        observed_ask = books.yes.best_ask
    else:
        observed_ask = books.no.best_ask if books.no else None
    price_basis = observed_ask if observed_ask is not None else decision.entry_price
    return min(float(price_basis or 0) + 0.03, 0.999)


def place_live_order(clob: DirectClob, config, decision, books, mode: str) -> Dict[str, Any]:
    assert decision.token_id
    if mode == "maker":
        assert decision.limit_price is not None
        result = clob.place_maker_buy(
            token_id=decision.token_id,
            amount_usd=decision.amount_usd,
            price=decision.limit_price,
            ttl_seconds=config.maker.ttl_seconds,
            post_only=config.maker.post_only,
        )
    else:
        result = clob.place_taker_buy(
            token_id=decision.token_id,
            amount_usd=decision.amount_usd,
            order_type=decision.order_type,
            price_limit=taker_price_limit(decision, books),
        )
    return normalize_order_result(result)


def refreshed_live_taker_candidate(config, wallet, clob: DirectClob, market, mode: str) -> tuple[Any, Dict[str, Any], Any, Dict[str, Any]]:
    try:
        books = clob.get_outcome_books(market.yes_token_id, market.no_token_id)
    except Exception as exc:
        payload = {"should_trade": False, "skip_reason": f"retry orderbook unavailable: {exc}", "market": {"question": market.question}}
        return None, payload, None, {"ok": False, "reason": payload["skip_reason"]}

    market_yes_price = clob.get_midpoint(market.yes_token_id)
    momentum = get_binance_momentum(config.asset, config.lookback_minutes)
    if not momentum:
        payload = {"should_trade": False, "skip_reason": "retry signal fetch failed", "market": {"question": market.question}}
        return None, payload, books, {"ok": False, "reason": payload["skip_reason"]}

    fee_rate = clob.get_fee_rate_bps(market.yes_token_id, config.default_taker_fee_rate_bps)
    decision = evaluate_trade(
        config,
        market,
        books,
        momentum,
        fee_rate_bps=fee_rate,
        mode=mode,
        market_yes_price=market_yes_price,
    )
    decision_dict = decision.to_dict()
    if not decision.should_trade:
        return None, decision_dict, books, {"ok": False, "reason": decision.skip_reason}

    quality_skip = live_quality_skip_reason(config, decision_dict)
    if quality_skip:
        decision_dict["should_trade"] = False
        decision_dict["skip_reason"] = quality_skip
        return None, decision_dict, books, {"ok": False, "reason": quality_skip}

    side_skip = live_experiment_cap_skip_reason(config, decision_dict)
    if side_skip:
        decision_dict["should_trade"] = False
        decision_dict["skip_reason"] = side_skip
        return None, decision_dict, books, {"ok": False, "reason": side_skip}

    risk = check_and_size(config, decision.amount_usd, user_address=wallet.funder_address)
    if not risk.ok:
        decision_dict["should_trade"] = False
        decision_dict["skip_reason"] = risk.reason
        return None, decision_dict, books, {"ok": False, "reason": risk.reason, "risk": risk.__dict__}

    decision.amount_usd = risk.amount_usd
    return decision, decision.to_dict(), books, {"ok": True, "risk": risk.__dict__}


def run_status() -> int:
    load_dotenv()
    wallet = load_wallet_config()
    clob = DirectClob(wallet)
    status = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "has_private_key": wallet.has_l1,
        "has_l2_creds": wallet.has_l2,
        "ready_for_trading": wallet.ready_for_trading,
        "signature_type": wallet.signature_type,
        "has_funder_address": bool(wallet.funder_address),
    }
    if wallet.has_l2:
        try:
            status["open_orders_count"] = len(clob.get_open_orders())
        except Exception as exc:
            status["open_orders_error"] = str(exc)
        try:
            status["balance_allowance"] = clob.get_balance_allowance()
        except Exception as exc:
            status["balance_allowance_error"] = str(exc)
    print_json(status)
    return 0


def run_once(args: argparse.Namespace) -> int:
    load_dotenv()
    config = load_config()
    wallet = load_wallet_config()
    live = bool(args.live)
    mode = args.mode

    if live:
        if not args.yes_i_understand:
            print("Live blocked: pass --yes-i-understand for an intentional one-shot live run.")
            return 2
        if not wallet.live_confirm:
            print("Live blocked: set DIRECT_LIVE_CONFIRM=YES in your environment for this shell.")
            return 2
        if not wallet.ready_for_trading:
            print("Live blocked: POLY_PRIVATE_KEY and POLY_API_* CLOB credentials are required.")
            return 2

    clob = DirectClob(wallet)
    markets = discover_fast_markets(config.asset, config.window, horizon_slots=args.limit_markets)
    record_late_shadow_observations(config, wallet, clob, markets, mode, live)
    market = choose_live_market(markets, config.min_time_remaining, config.max_time_remaining, config.window)
    if not market:
        payload = {
            "status": "skipped",
            "skip_reason": "no live market in configured time window",
            "markets_seen": len(markets),
        }
        print_json(payload)
        return 0

    try:
        books = clob.get_outcome_books(market.yes_token_id, market.no_token_id)
    except Exception as exc:
        payload = {"status": "skipped", "skip_reason": f"orderbook unavailable: {exc}", "market": market.question}
        print_json(payload)
        return 0
    market_yes_price = clob.get_midpoint(market.yes_token_id)

    momentum = get_binance_momentum(config.asset, config.lookback_minutes)
    if not momentum:
        payload = {"status": "skipped", "skip_reason": "signal fetch failed", "market": market.question}
        print_json(payload)
        return 0

    fee_rate = clob.get_fee_rate_bps(market.yes_token_id, config.default_taker_fee_rate_bps)
    decision = evaluate_trade(
        config,
        market,
        books,
        momentum,
        fee_rate_bps=fee_rate,
        mode=mode,
        market_yes_price=market_yes_price,
    )
    decision_dict = decision.to_dict()

    if not decision.should_trade:
        record_decision(decision_dict, live=live, status="skipped")
        print_json({"status": "skipped", "decision": decision_dict})
        return 0

    condition_id = (decision_dict.get("market") or {}).get("condition_id")
    if live and condition_id and has_live_success_for_market_today(condition_id):
        decision_dict["skip_reason"] = "already traded market live today"
        record_decision(decision_dict, live=live, status="skipped")
        print_json({"status": "skipped", "decision": decision_dict})
        return 0

    if live and mode == "taker":
        quality_skip = live_quality_skip_reason(config, decision_dict)
        if quality_skip:
            decision_dict["should_trade"] = False
            decision_dict["skip_reason"] = quality_skip
            record_decision(decision_dict, live=live, status="skipped")
            print_json({"status": "skipped", "decision": decision_dict})
            return 0
        side_skip = live_experiment_cap_skip_reason(config, decision_dict)
        if side_skip:
            decision_dict["should_trade"] = False
            decision_dict["skip_reason"] = side_skip
            record_decision(decision_dict, live=live, status="skipped")
            print_json({"status": "skipped", "decision": decision_dict})
            return 0

    risk = check_and_size(config, decision.amount_usd, user_address=wallet.funder_address if live else None)
    if not risk.ok:
        decision_dict["skip_reason"] = risk.reason
        record_decision(decision_dict, live=live, status="skipped")
        print_json({"status": "skipped", "risk": risk.__dict__, "decision": decision_dict})
        return 0
    decision.amount_usd = risk.amount_usd
    decision_dict = decision.to_dict()
    record_decision(decision_dict, live=live, status="candidate")

    if not live:
        print_json(
            {
                "status": "dry_run_candidate",
                "mode": mode,
                "risk": risk.__dict__,
                "decision": decision_dict,
            }
        )
        return 0

    attempt_results = []
    try:
        result_dict = place_live_order(clob, config, decision, books, mode)
    except Exception as exc:
        result_dict = normalize_order_exception(exc)
    result_dict["attempt"] = 1
    record_attempt(decision_dict, result_dict, live=True)
    attempt_results.append({"decision": decision_dict, "result": result_dict})
    success = order_success(result_dict)

    if (
        not success
        and mode == "taker"
        and decision.order_type in (OrderType.FAK, OrderType.FOK)
        and retryable_taker_error(result_dict)
    ):
        time.sleep(1)
        retry_decision, retry_decision_dict, retry_books, retry_meta = refreshed_live_taker_candidate(
            config, wallet, clob, market, mode
        )
        if retry_decision is None or retry_books is None:
            record_decision(retry_decision_dict, live=live, status="skipped")
            result_dict["retry"] = {"status": "skipped", **retry_meta}
        else:
            record_decision(retry_decision_dict, live=live, status="candidate_retry")
            try:
                retry_result = place_live_order(clob, config, retry_decision, retry_books, mode)
            except Exception as exc:
                retry_result = normalize_order_exception(exc)
            retry_result["attempt"] = 2
            record_attempt(retry_decision_dict, retry_result, live=True)
            attempt_results.append({"decision": retry_decision_dict, "result": retry_result})
            if order_success(retry_result):
                decision = retry_decision
                decision_dict = retry_decision_dict
                result_dict = retry_result
                success = True

    if success:
        mark_live_success(decision.amount_usd)

    if args.wait_cancel and mode == "maker":
        order_id = result_dict.get("orderID") or result_dict.get("order_id") or result_dict.get("id")
        if order_id:
            time.sleep(max(int(args.wait_cancel), 1))
            try:
                cancel = clob.cancel_order(order_id)
                result_dict["cancel_after_wait"] = normalize_order_result(cancel)
            except Exception as exc:
                result_dict["cancel_after_wait_error"] = str(exc)

    print_json(
        {
            "status": "live_sent" if success else "live_error",
            "decision": decision_dict,
            "result": result_dict,
            "attempts": attempt_results,
        }
    )
    return 0 if success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct Polymarket FastLoop BTC bot, no Simmer SDK.")
    parser.add_argument("--live", action="store_true", help="Place a real Polymarket order. Dry-run by default.")
    parser.add_argument("--yes-i-understand", action="store_true", help="Required live confirmation flag.")
    parser.add_argument("--mode", choices=["taker", "maker"], default="taker", help="Execution style to evaluate/run.")
    parser.add_argument("--status", action="store_true", help="Check direct wallet/CLOB status and exit.")
    parser.add_argument("--limit-markets", type=int, default=14, help="Fast-market slots to inspect.")
    parser.add_argument("--wait-cancel", type=int, default=0, help="For live maker, wait N seconds then cancel the order.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.status:
        return run_status()
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
