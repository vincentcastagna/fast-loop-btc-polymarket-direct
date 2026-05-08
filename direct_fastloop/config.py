from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"
LOG_DIR = ROOT / "logs"


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Tiny .env loader so the bot has no extra dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return int(value)


@dataclass
class MakerConfig:
    enabled: bool = False
    live_enabled: bool = False
    max_order_usd: float = 5.0
    max_daily_orders: int = 2
    min_time_remaining: int = 75
    bid_offset: float = 0.03
    min_edge_to_bid: float = 0.12
    min_bid: float = 0.20
    max_bid: float = 0.65
    max_current_ask: float = 0.80
    ttl_seconds: int = 60
    post_only: bool = True


@dataclass
class BotConfig:
    entry_threshold: float
    min_momentum_pct: float
    probe_min_momentum_pct: float
    asset: str
    window: str
    signal_source: str
    strategy_mode: str
    allowed_side: str
    lookback_minutes: int
    min_time_remaining: int
    max_time_remaining: int
    max_position: float
    daily_budget: float
    max_daily_trades: int
    max_daily_losses: int
    order_type: str
    volume_confidence: bool
    use_alpha_filter: bool
    min_signal_score: float
    use_empirical_guard: bool
    empirical_trade_lookback: int
    empirical_min_samples: int
    empirical_min_avg_pnl: float
    empirical_hard_veto_avg_pnl: float
    probe_position_multiplier: float
    probe_min_signal_score: float
    min_trend_ratio: float
    min_close_location: float
    min_setup_score: float
    min_setup_score_yes: float
    min_setup_score_no: float
    min_setup_score_yes_trade: float
    min_contract_price: float
    max_contract_price: float
    max_entry_price: float
    max_entry_price_yes: float
    max_entry_price_no: float
    max_entry_price_yes_premium: float
    allow_contrarian_fair_value: bool
    contrarian_min_edge: float
    min_model_edge: float
    min_model_edge_yes: float
    min_model_edge_no: float
    min_aligned_edge: float
    min_confirmation_pct: float
    require_confirmation: bool
    require_signal_consensus: bool
    min_strike_distance_pct: float
    max_strike_distance_pct: float
    late_stage_seconds: int
    late_stage_buffer_pct: float
    use_fair_value: bool
    fair_value_min_edge: float
    btc_annual_vol: float
    default_taker_fee_rate_bps: int
    max_spread_pct: float
    max_spread_cents: float
    min_shares_per_order: int
    direct_daily_budget: float
    direct_max_daily_trades: int
    direct_max_daily_losses: int
    direct_daily_stop_loss: float
    direct_guard_reset_utc: Optional[str]
    direct_live_quality_filter: bool
    direct_live_require_micro_side: bool
    direct_live_min_recent_move_pct: float
    direct_live_min_volume_ratio: float
    direct_live_block_negative_one_min: bool
    direct_live_no_enabled: bool
    direct_live_max_daily_no_trades: int
    direct_live_strict_no_enabled: bool
    direct_live_strict_no_max_daily_trades: int
    direct_live_strict_no_amount_usd: float
    direct_live_strict_no_min_signal_score: float
    direct_live_strict_no_min_setup_score: float
    direct_live_strict_no_min_trend_ratio: float
    direct_live_strict_no_min_volume_ratio: float
    direct_live_strict_no_max_entry_price: float
    direct_live_strict_no_max_recent_move_pct: float
    direct_live_strict_no_max_one_min_move_pct: float
    direct_live_choppy_yes_enabled: bool
    direct_live_max_daily_choppy_yes_trades: int
    direct_live_choppy_yes_min_trend_ratio: float
    direct_live_choppy_yes_min_signal_score: float
    direct_live_choppy_yes_min_setup_score: float
    direct_live_entry_confirmation_enabled: bool
    direct_live_entry_confirmation_min_wait_seconds: int
    direct_live_entry_confirmation_min_remaining_seconds: int
    direct_live_entry_confirmation_max_age_seconds: int
    direct_live_entry_confirmation_max_price_slippage_cents: float
    direct_daily_profit_lock_enabled: bool
    direct_daily_profit_lock_start: float
    direct_daily_profit_lock_giveback: float
    direct_shadow_late_enabled: bool
    direct_shadow_late_min_time_remaining: int
    direct_shadow_late_max_time_remaining: int
    direct_shadow_exit_enabled: bool
    direct_live_exit_enabled: bool
    direct_live_exit_shadow_only: bool
    direct_live_exit_max_remaining_seconds: int
    direct_live_exit_min_remaining_seconds: int
    direct_live_exit_max_unrealized_pnl: float
    direct_live_exit_min_bad_reasons: int
    direct_live_exit_slippage_cents: float
    direct_live_exit_min_bid: float
    direct_live_exit_yes_only: bool
    maker: MakerConfig


@dataclass
class WalletConfig:
    private_key: Optional[str]
    api_key: Optional[str]
    api_secret: Optional[str]
    api_passphrase: Optional[str]
    signature_type: Optional[int]
    funder_address: Optional[str]
    live_confirm: bool
    chain_id: int = 137
    host: str = "https://clob.polymarket.com"

    @property
    def has_l1(self) -> bool:
        return bool(self.private_key)

    @property
    def has_l2(self) -> bool:
        return bool(self.api_key and self.api_secret and self.api_passphrase)

    @property
    def ready_for_trading(self) -> bool:
        return self.has_l1 and self.has_l2


def load_config(path: Path = CONFIG_PATH) -> BotConfig:
    payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    maker_payload = payload.get("maker", {})
    maker = MakerConfig(
        enabled=bool(payload.get("use_passive_maker", maker_payload.get("enabled", False))),
        live_enabled=bool(payload.get("passive_maker_live_enabled", maker_payload.get("live_enabled", False))),
        max_order_usd=float(payload.get("passive_maker_max_order_usd", maker_payload.get("max_order_usd", 5.0))),
        max_daily_orders=int(payload.get("passive_maker_max_daily_orders", maker_payload.get("max_daily_orders", 2))),
        min_time_remaining=int(payload.get("passive_maker_min_time_remaining", maker_payload.get("min_time_remaining", 75))),
        bid_offset=float(payload.get("passive_maker_bid_offset", maker_payload.get("bid_offset", 0.03))),
        min_edge_to_bid=float(payload.get("passive_maker_min_edge_to_bid", maker_payload.get("min_edge_to_bid", 0.12))),
        min_bid=float(payload.get("passive_maker_min_bid", maker_payload.get("min_bid", 0.20))),
        max_bid=float(payload.get("passive_maker_max_bid", maker_payload.get("max_bid", 0.65))),
        max_current_ask=float(payload.get("passive_maker_max_current_ask", maker_payload.get("max_current_ask", 0.80))),
        ttl_seconds=int(payload.get("passive_maker_order_ttl_seconds", maker_payload.get("ttl_seconds", 60))),
        post_only=bool(maker_payload.get("post_only", True)),
    )
    return BotConfig(
        entry_threshold=float(payload.get("entry_threshold", 0.01)),
        min_momentum_pct=float(payload.get("min_momentum_pct", 0.0)),
        probe_min_momentum_pct=float(payload.get("probe_min_momentum_pct", 0.0)),
        asset=os.environ.get("DIRECT_FASTLOOP_ASSET", payload.get("asset", "BTC")).upper(),
        window=os.environ.get("DIRECT_FASTLOOP_WINDOW", payload.get("window", "5m")),
        signal_source=payload.get("signal_source", "binance"),
        strategy_mode=payload.get("strategy_mode", "momentum_first"),
        allowed_side=payload.get("allowed_side", "yes_only"),
        lookback_minutes=_env_int("DIRECT_FASTLOOP_LOOKBACK_MINUTES", int(payload.get("lookback_minutes", 5))),
        min_time_remaining=_env_int("DIRECT_FASTLOOP_MIN_TIME", int(payload.get("min_time_remaining", 45))),
        max_time_remaining=_env_int("DIRECT_FASTLOOP_MAX_TIME", int(payload.get("max_time_remaining", 150))),
        max_position=_env_float("DIRECT_FASTLOOP_MAX_POSITION_USD", float(payload.get("max_position", 5.0))),
        daily_budget=_env_float("DIRECT_FASTLOOP_DAILY_BUDGET_USD", float(payload.get("daily_budget", 15.0))),
        max_daily_trades=_env_int("DIRECT_FASTLOOP_MAX_DAILY_TRADES", int(payload.get("max_daily_trades", 3))),
        max_daily_losses=int(payload.get("max_daily_losses", 0)),
        order_type=os.environ.get("DIRECT_FASTLOOP_ORDER_TYPE", payload.get("order_type", "FAK")).upper(),
        volume_confidence=bool(payload.get("volume_confidence", False)),
        use_alpha_filter=bool(payload.get("use_alpha_filter", True)),
        min_signal_score=float(payload.get("min_signal_score", 0.4)),
        use_empirical_guard=bool(payload.get("use_empirical_guard", False)),
        empirical_trade_lookback=int(payload.get("empirical_trade_lookback", 60)),
        empirical_min_samples=int(payload.get("empirical_min_samples", 3)),
        empirical_min_avg_pnl=float(payload.get("empirical_min_avg_pnl", 0.0)),
        empirical_hard_veto_avg_pnl=float(payload.get("empirical_hard_veto_avg_pnl", -1.0)),
        probe_position_multiplier=float(payload.get("probe_position_multiplier", 0.5)),
        probe_min_signal_score=float(payload.get("probe_min_signal_score", 0.5)),
        min_trend_ratio=float(payload.get("min_trend_ratio", 0.75)),
        min_close_location=float(payload.get("min_close_location", 0.85)),
        min_setup_score=float(payload.get("min_setup_score", 0.0)),
        min_setup_score_yes=float(payload.get("min_setup_score_yes", 0.0)),
        min_setup_score_no=float(payload.get("min_setup_score_no", 0.0)),
        min_setup_score_yes_trade=float(payload.get("min_setup_score_yes_trade", 0.55)),
        min_contract_price=float(payload.get("min_contract_price", 0.2)),
        max_contract_price=float(payload.get("max_contract_price", 0.8)),
        max_entry_price=float(payload.get("max_entry_price", 0.6)),
        max_entry_price_yes=float(payload.get("max_entry_price_yes", 0.75)),
        max_entry_price_no=float(payload.get("max_entry_price_no", 0.65)),
        max_entry_price_yes_premium=float(payload.get("max_entry_price_yes_premium", 0.75)),
        allow_contrarian_fair_value=bool(payload.get("allow_contrarian_fair_value", False)),
        contrarian_min_edge=float(payload.get("contrarian_min_edge", 0.08)),
        min_model_edge=float(payload.get("min_model_edge", 0.05)),
        min_model_edge_yes=float(payload.get("min_model_edge_yes", payload.get("min_model_edge", 0.05))),
        min_model_edge_no=float(payload.get("min_model_edge_no", payload.get("min_model_edge", 0.05))),
        min_aligned_edge=float(payload.get("min_aligned_edge", 0.05)),
        min_confirmation_pct=float(payload.get("min_confirmation_pct", 0.02)),
        require_confirmation=bool(payload.get("require_confirmation", True)),
        require_signal_consensus=bool(payload.get("require_signal_consensus", True)),
        min_strike_distance_pct=float(payload.get("min_strike_distance_pct", 0.05)),
        max_strike_distance_pct=float(payload.get("max_strike_distance_pct", 0.05)),
        late_stage_seconds=int(payload.get("late_stage_seconds", 20)),
        late_stage_buffer_pct=float(payload.get("late_stage_buffer_pct", 0.12)),
        use_fair_value=bool(payload.get("use_fair_value", False)),
        fair_value_min_edge=float(payload.get("fair_value_min_edge", 0.025)),
        btc_annual_vol=float(payload.get("btc_annual_vol", 0.65)),
        default_taker_fee_rate_bps=int(payload.get("default_taker_fee_rate_bps", 0)),
        max_spread_pct=float(payload.get("max_spread_pct", 0.10)),
        max_spread_cents=float(payload.get("max_spread_cents", 2.0)),
        min_shares_per_order=int(payload.get("min_shares_per_order", 5)),
        direct_daily_budget=_env_float("DIRECT_FASTLOOP_DIRECT_DAILY_BUDGET_USD", float(payload.get("direct_daily_budget", 15.0))),
        direct_max_daily_trades=_env_int("DIRECT_FASTLOOP_DIRECT_MAX_DAILY_TRADES", int(payload.get("direct_max_daily_trades", 3))),
        direct_max_daily_losses=int(payload.get("direct_max_daily_losses", 2)),
        direct_daily_stop_loss=float(payload.get("direct_daily_stop_loss", -7.5)),
        direct_guard_reset_utc=payload.get("direct_guard_reset_utc") or None,
        direct_live_quality_filter=bool(payload.get("direct_live_quality_filter", False)),
        direct_live_require_micro_side=bool(payload.get("direct_live_require_micro_side", True)),
        direct_live_min_recent_move_pct=float(payload.get("direct_live_min_recent_move_pct", 0.02)),
        direct_live_min_volume_ratio=float(payload.get("direct_live_min_volume_ratio", 0.10)),
        direct_live_block_negative_one_min=bool(payload.get("direct_live_block_negative_one_min", True)),
        direct_live_no_enabled=bool(payload.get("direct_live_no_enabled", False)),
        direct_live_max_daily_no_trades=int(payload.get("direct_live_max_daily_no_trades", 0)),
        direct_live_strict_no_enabled=bool(payload.get("direct_live_strict_no_enabled", False)),
        direct_live_strict_no_max_daily_trades=int(payload.get("direct_live_strict_no_max_daily_trades", 0)),
        direct_live_strict_no_amount_usd=float(payload.get("direct_live_strict_no_amount_usd", 3.0)),
        direct_live_strict_no_min_signal_score=float(payload.get("direct_live_strict_no_min_signal_score", 0.45)),
        direct_live_strict_no_min_setup_score=float(payload.get("direct_live_strict_no_min_setup_score", 0.50)),
        direct_live_strict_no_min_trend_ratio=float(payload.get("direct_live_strict_no_min_trend_ratio", 0.75)),
        direct_live_strict_no_min_volume_ratio=float(payload.get("direct_live_strict_no_min_volume_ratio", 0.50)),
        direct_live_strict_no_max_entry_price=float(payload.get("direct_live_strict_no_max_entry_price", 0.55)),
        direct_live_strict_no_max_recent_move_pct=float(payload.get("direct_live_strict_no_max_recent_move_pct", 0.0)),
        direct_live_strict_no_max_one_min_move_pct=float(payload.get("direct_live_strict_no_max_one_min_move_pct", 0.0)),
        direct_live_choppy_yes_enabled=bool(payload.get("direct_live_choppy_yes_enabled", False)),
        direct_live_max_daily_choppy_yes_trades=int(payload.get("direct_live_max_daily_choppy_yes_trades", 0)),
        direct_live_choppy_yes_min_trend_ratio=float(payload.get("direct_live_choppy_yes_min_trend_ratio", 0.50)),
        direct_live_choppy_yes_min_signal_score=float(payload.get("direct_live_choppy_yes_min_signal_score", 0.50)),
        direct_live_choppy_yes_min_setup_score=float(payload.get("direct_live_choppy_yes_min_setup_score", 0.55)),
        direct_live_entry_confirmation_enabled=bool(payload.get("direct_live_entry_confirmation_enabled", False)),
        direct_live_entry_confirmation_min_wait_seconds=int(
            payload.get("direct_live_entry_confirmation_min_wait_seconds", 25)
        ),
        direct_live_entry_confirmation_min_remaining_seconds=int(
            payload.get("direct_live_entry_confirmation_min_remaining_seconds", 95)
        ),
        direct_live_entry_confirmation_max_age_seconds=int(
            payload.get("direct_live_entry_confirmation_max_age_seconds", 130)
        ),
        direct_live_entry_confirmation_max_price_slippage_cents=float(
            payload.get("direct_live_entry_confirmation_max_price_slippage_cents", 3.0)
        ),
        direct_daily_profit_lock_enabled=bool(payload.get("direct_daily_profit_lock_enabled", False)),
        direct_daily_profit_lock_start=float(payload.get("direct_daily_profit_lock_start", 5.0)),
        direct_daily_profit_lock_giveback=float(payload.get("direct_daily_profit_lock_giveback", 3.5)),
        direct_shadow_late_enabled=bool(payload.get("direct_shadow_late_enabled", False)),
        direct_shadow_late_min_time_remaining=int(payload.get("direct_shadow_late_min_time_remaining", 15)),
        direct_shadow_late_max_time_remaining=int(payload.get("direct_shadow_late_max_time_remaining", 45)),
        direct_shadow_exit_enabled=bool(payload.get("direct_shadow_exit_enabled", False)),
        direct_live_exit_enabled=bool(payload.get("direct_live_exit_enabled", False)),
        direct_live_exit_shadow_only=bool(payload.get("direct_live_exit_shadow_only", True)),
        direct_live_exit_max_remaining_seconds=int(payload.get("direct_live_exit_max_remaining_seconds", 90)),
        direct_live_exit_min_remaining_seconds=int(payload.get("direct_live_exit_min_remaining_seconds", 15)),
        direct_live_exit_max_unrealized_pnl=float(payload.get("direct_live_exit_max_unrealized_pnl", -1.25)),
        direct_live_exit_min_bad_reasons=int(payload.get("direct_live_exit_min_bad_reasons", 2)),
        direct_live_exit_slippage_cents=float(payload.get("direct_live_exit_slippage_cents", 3.0)),
        direct_live_exit_min_bid=float(payload.get("direct_live_exit_min_bid", 0.05)),
        direct_live_exit_yes_only=bool(payload.get("direct_live_exit_yes_only", True)),
        maker=maker,
    )


def load_wallet_config() -> WalletConfig:
    sig_raw = os.environ.get("POLY_SIGNATURE_TYPE")
    return WalletConfig(
        private_key=os.environ.get("POLY_PRIVATE_KEY") or None,
        api_key=os.environ.get("POLY_API_KEY") or None,
        api_secret=os.environ.get("POLY_API_SECRET") or None,
        api_passphrase=os.environ.get("POLY_API_PASSPHRASE") or None,
        signature_type=int(sig_raw) if sig_raw not in (None, "") else None,
        funder_address=os.environ.get("POLY_FUNDER_ADDRESS") or None,
        live_confirm=(os.environ.get("DIRECT_LIVE_CONFIRM") or "").upper() == "YES",
    )
