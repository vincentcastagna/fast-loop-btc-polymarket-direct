from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    MarketOrderArgsV2,
    OpenOrderParams,
    OrderArgsV2,
    OrderPayload,
    OrderType,
)

from .config import WalletConfig


BUY = "BUY"


@dataclass
class BookSide:
    best_bid: Optional[float]
    best_ask: Optional[float]
    bid_depth_usd: float
    ask_depth_usd: float
    spread_cents: Optional[float]
    tick_size: Optional[str]
    min_order_size: Optional[float]


@dataclass
class OutcomeBooks:
    yes: BookSide
    no: Optional[BookSide]


class DirectClob:
    def __init__(self, wallet: WalletConfig):
        self.wallet = wallet
        creds = None
        if wallet.has_l2:
            creds = ApiCreds(
                api_key=wallet.api_key or "",
                api_secret=wallet.api_secret or "",
                api_passphrase=wallet.api_passphrase or "",
            )
        self.client = ClobClient(
            host=wallet.host,
            chain_id=wallet.chain_id,
            key=wallet.private_key,
            creds=creds,
            signature_type=wallet.signature_type,
            funder=wallet.funder_address,
            retry_on_error=True,
        )

    @staticmethod
    def derive_api_creds(wallet: WalletConfig) -> ApiCreds:
        if not wallet.private_key:
            raise RuntimeError("POLY_PRIVATE_KEY is required to derive CLOB credentials")
        client = ClobClient(
            host=wallet.host,
            chain_id=wallet.chain_id,
            key=wallet.private_key,
            signature_type=wallet.signature_type,
            funder=wallet.funder_address,
        )
        return client.create_or_derive_api_key()

    def get_book_side(self, token_id: str) -> Optional[BookSide]:
        raw = self.client.get_order_book(token_id)
        if not raw:
            return None
        data = raw if isinstance(raw, dict) else raw.__dict__
        bids = data.get("bids") or []
        asks = data.get("asks") or []

        def price_size(level: Any) -> tuple[float, float]:
            if isinstance(level, dict):
                return float(level.get("price", 0)), float(level.get("size", 0))
            return float(getattr(level, "price", 0)), float(getattr(level, "size", 0))

        parsed_bids = [price_size(x) for x in bids]
        parsed_asks = [price_size(x) for x in asks]
        best_bid = max((p for p, _ in parsed_bids), default=None)
        best_ask = min((p for p, _ in parsed_asks), default=None)
        bid_depth = sum(p * s for p, s in sorted(parsed_bids, reverse=True)[:5])
        ask_depth = sum(p * s for p, s in sorted(parsed_asks)[:5])
        spread = None
        if best_bid is not None and best_ask is not None:
            spread = (best_ask - best_bid) * 100
        min_size = data.get("min_order_size")
        try:
            min_order_size = float(min_size) if min_size is not None else None
        except (TypeError, ValueError):
            min_order_size = None
        return BookSide(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_depth_usd=bid_depth,
            ask_depth_usd=ask_depth,
            spread_cents=spread,
            tick_size=data.get("tick_size"),
            min_order_size=min_order_size,
        )

    def get_outcome_books(self, yes_token_id: str, no_token_id: Optional[str]) -> OutcomeBooks:
        yes = self.get_book_side(yes_token_id)
        if not yes:
            raise RuntimeError("YES orderbook unavailable")
        no = self.get_book_side(no_token_id) if no_token_id else None
        return OutcomeBooks(yes=yes, no=no)

    def get_midpoint(self, token_id: str) -> Optional[float]:
        raw = self.client.get_midpoint(token_id)
        if raw is None:
            return None
        if isinstance(raw, dict):
            value = raw.get("mid") or raw.get("midpoint")
        else:
            value = getattr(raw, "mid", raw)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_fee_rate_bps(self, token_id: str, fallback: int) -> int:
        try:
            value = self.client.get_fee_rate_bps(token_id)
            return int(float(value or fallback))
        except Exception:
            return fallback

    def get_open_orders(self, token_id: Optional[str] = None) -> list:
        params = OpenOrderParams(asset_id=token_id) if token_id else None
        return self.client.get_open_orders(params=params, only_first_page=True)

    def get_balance_allowance(self) -> Dict[str, Any]:
        return self.client.get_balance_allowance(
            BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self.wallet.signature_type if self.wallet.signature_type is not None else -1,
            )
        )

    def place_taker_buy(
        self,
        token_id: str,
        amount_usd: float,
        order_type: str = OrderType.FAK,
        price_limit: float = 0,
        user_usdc_balance: float = 0,
    ) -> Dict[str, Any]:
        args = MarketOrderArgsV2(
            token_id=token_id,
            amount=round(float(amount_usd), 2),
            side=BUY,
            price=round(float(price_limit), 4) if price_limit else 0,
            order_type=order_type,
            user_usdc_balance=float(user_usdc_balance or 0),
        )
        return self.client.create_and_post_market_order(args, order_type=order_type)

    def place_maker_buy(
        self,
        token_id: str,
        amount_usd: float,
        price: float,
        ttl_seconds: int = 0,
        post_only: bool = True,
    ) -> Dict[str, Any]:
        size = round(float(amount_usd) / float(price), 5)
        expiration = 0
        if ttl_seconds and ttl_seconds > 0:
            import time

            expiration = int(time.time()) + int(ttl_seconds)
        args = OrderArgsV2(
            token_id=token_id,
            price=round(float(price), 4),
            size=size,
            side=BUY,
            expiration=expiration,
        )
        order_type = OrderType.GTD if expiration else OrderType.GTC
        return self.client.create_and_post_order(args, order_type=order_type, post_only=post_only)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return self.client.cancel_order(OrderPayload(orderID=order_id))
