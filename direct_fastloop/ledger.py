from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from .config import LOG_DIR


STATE_PATH = LOG_DIR / "daily_state.json"
DECISION_LOG = LOG_DIR / "direct_decisions.jsonl"
ORDER_LOG = LOG_DIR / "direct_orders.jsonl"
SHADOW_LOG = LOG_DIR / "direct_shadow_decisions.jsonl"


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=_json_default, separators=(",", ":")) + "\n")


def today_key() -> str:
    # Local ET is UTC-4 for this May 2026 run. Keep accounting local and explicit.
    return datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")


def _local_date_key(timestamp_utc: str) -> str:
    ts = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=-4)))
    return ts.strftime("%Y-%m-%d")


def load_daily_state() -> Dict[str, Any]:
    key = today_key()
    if not STATE_PATH.exists():
        return {"date": key, "spent": 0.0, "trades": 0}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"date": key, "spent": 0.0, "trades": 0}
    if data.get("date") != key:
        return {"date": key, "spent": 0.0, "trades": 0}
    return {
        "date": key,
        "spent": float(data.get("spent") or 0),
        "trades": int(data.get("trades") or 0),
    }


def save_daily_state(state: Dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def record_attempt(decision: Dict[str, Any], result: Dict[str, Any], live: bool) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "live": live,
        "decision": decision,
        "result": result,
    }
    append_jsonl(ORDER_LOG, record)


def record_decision(decision: Dict[str, Any], live: bool, status: str) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "live": live,
        "status": status,
        **decision,
    }
    append_jsonl(DECISION_LOG, record)


def record_shadow(payload: Dict[str, Any], live: bool, status: str) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "live": live,
        "status": status,
        **payload,
    }
    append_jsonl(SHADOW_LOG, record)


def _order_result_success(result: Dict[str, Any]) -> bool:
    if result.get("success") is True:
        return True
    if result.get("success") is False or result.get("error") or result.get("exception_type"):
        return False
    status = str(result.get("status") or "").lower()
    if status in {"matched", "live"}:
        return True
    return bool(result.get("orderID") or result.get("order_id") or result.get("id"))


def has_live_success_for_market_today(condition_id: str) -> bool:
    return get_live_success_for_market_today(condition_id) is not None


def get_live_success_for_market_today(condition_id: str) -> Dict[str, Any] | None:
    if not condition_id or not ORDER_LOG.exists():
        return None
    key = today_key()
    for raw in ORDER_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not record.get("live"):
            continue
        try:
            if _local_date_key(record.get("timestamp_utc", "")) != key:
                continue
        except ValueError:
            continue
        decision = record.get("decision") or {}
        market = decision.get("market") or {}
        if market.get("condition_id") != condition_id:
            continue
        result = record.get("result") or {}
        if _order_result_success(result):
            return record
    return None


def count_live_successes_today(side: str | None = None, market_flag: str | None = None) -> int:
    if not ORDER_LOG.exists():
        return 0
    key = today_key()
    count = 0
    for raw in ORDER_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not record.get("live"):
            continue
        try:
            if _local_date_key(record.get("timestamp_utc", "")) != key:
                continue
        except ValueError:
            continue
        decision = record.get("decision") or {}
        if side and decision.get("side") != side:
            continue
        market = decision.get("market") or {}
        if market_flag and not market.get(market_flag):
            continue
        result = record.get("result") or {}
        if _order_result_success(result):
            count += 1
    return count
