from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen

from .config import LOG_DIR


DEFAULT_RPC_URL = "https://polygon-bor-rpc.publicnode.com"

POLYGON_FEEDS = {
    # Official Chainlink Polygon Mainnet proxy feeds.
    "BTC": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
    "ETH": "0xF9680D99D6C9589e2a93a78A04a279e509205945",
}

LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"
DECIMALS_SELECTOR = "0x313ce567"

_DECIMALS_CACHE: dict[tuple[str, str], int] = {}


@dataclass
class ChainlinkPrice:
    asset: str
    feed_address: str
    price: float
    decimals: int
    round_id: int
    updated_at: datetime
    observed_at: datetime
    age_seconds: float

    def to_sample(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "feed_address": self.feed_address,
            "price": self.price,
            "decimals": self.decimals,
            "round_id": self.round_id,
            "updated_at_utc": self.updated_at.isoformat(),
            "observed_at_utc": self.observed_at.isoformat(),
            "age_seconds": self.age_seconds,
        }


def default_feed_address(asset: str) -> Optional[str]:
    return POLYGON_FEEDS.get(asset.upper())


def configured_rpc_url(rpc_url: Optional[str] = None) -> str:
    return (
        rpc_url
        or os.environ.get("CHAINLINK_RPC_URL")
        or os.environ.get("POLYGON_RPC_URL")
        or DEFAULT_RPC_URL
    )


def configured_feed_address(asset: str, feed_address: Optional[str] = None) -> Optional[str]:
    if feed_address:
        return feed_address
    env_key = f"CHAINLINK_{asset.upper()}_USD_FEED"
    return os.environ.get(env_key) or default_feed_address(asset)


def sample_path(asset: str) -> Path:
    return LOG_DIR / f"chainlink_{asset.lower()}_samples.jsonl"


def _rpc_call(rpc_url: str, method: str, params: list[Any], timeout: int = 8) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    request = Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "fastloop-chainlink/0.1"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("result")


def _clean_hex(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("RPC result is not hex")
    return value[2:]


def _decode_uint256(word: str) -> int:
    return int(word, 16)


def _decode_int256(word: str) -> int:
    value = int(word, 16)
    if value >= 2**255:
        value -= 2**256
    return value


def _decode_words(result: str, count: int) -> list[str]:
    raw = _clean_hex(result)
    if len(raw) < 64 * count:
        raise ValueError("RPC result too short")
    return [raw[index * 64 : (index + 1) * 64] for index in range(count)]


def read_decimals(asset: str, rpc_url: Optional[str] = None, feed_address: Optional[str] = None) -> int:
    asset = asset.upper()
    url = configured_rpc_url(rpc_url)
    address = configured_feed_address(asset, feed_address)
    if not address:
        raise RuntimeError(f"No Chainlink feed configured for {asset}")
    key = (url, address.lower())
    cached = _DECIMALS_CACHE.get(key)
    if cached is not None:
        return cached
    result = _rpc_call(url, "eth_call", [{"to": address, "data": DECIMALS_SELECTOR}, "latest"])
    decimals = _decode_uint256(_decode_words(result, 1)[0])
    _DECIMALS_CACHE[key] = decimals
    return decimals


def read_latest_price(
    asset: str,
    *,
    rpc_url: Optional[str] = None,
    feed_address: Optional[str] = None,
    timeout: int = 8,
) -> ChainlinkPrice:
    asset = asset.upper()
    url = configured_rpc_url(rpc_url)
    address = configured_feed_address(asset, feed_address)
    if not address:
        raise RuntimeError(f"No Chainlink feed configured for {asset}")

    result = _rpc_call(url, "eth_call", [{"to": address, "data": LATEST_ROUND_DATA_SELECTOR}, "latest"], timeout=timeout)
    words = _decode_words(result, 5)
    round_id = _decode_uint256(words[0])
    answer = _decode_int256(words[1])
    updated_at_epoch = _decode_uint256(words[3])
    if answer <= 0:
        raise RuntimeError(f"Chainlink {asset}/USD answer is non-positive")
    if updated_at_epoch <= 0:
        raise RuntimeError(f"Chainlink {asset}/USD updatedAt is empty")

    decimals = read_decimals(asset, rpc_url=url, feed_address=address)
    observed_at = datetime.now(timezone.utc)
    updated_at = datetime.fromtimestamp(updated_at_epoch, tz=timezone.utc)
    return ChainlinkPrice(
        asset=asset,
        feed_address=address,
        price=answer / (10**decimals),
        decimals=decimals,
        round_id=round_id,
        updated_at=updated_at,
        observed_at=observed_at,
        age_seconds=max(0.0, (observed_at - updated_at).total_seconds()),
    )


def append_sample(price: ChainlinkPrice) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with sample_path(price.asset).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(price.to_sample(), separators=(",", ":")) + "\n")


def parse_sample_time(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def read_samples(asset: str, max_lines: int = 2000) -> list[dict[str, Any]]:
    path = sample_path(asset.upper())
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        observed_at = parse_sample_time(row.get("observed_at_utc"))
        price = row.get("price")
        if observed_at is None or price is None:
            continue
        try:
            row["_observed_at"] = observed_at
            row["_price"] = float(price)
        except (TypeError, ValueError):
            continue
        rows.append(row)
    rows.sort(key=lambda item: item["_observed_at"])
    return rows


def observe_latest_sample(
    asset: str,
    *,
    rpc_url: Optional[str] = None,
    feed_address: Optional[str] = None,
    max_feed_age_seconds: int = 180,
) -> Optional[ChainlinkPrice]:
    try:
        price = read_latest_price(asset, rpc_url=rpc_url, feed_address=feed_address)
    except Exception:
        return None
    append_sample(price)
    if max_feed_age_seconds > 0 and price.age_seconds > max_feed_age_seconds:
        return None
    return price
