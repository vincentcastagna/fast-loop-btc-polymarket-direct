from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop import chainlink


def test_decodes_latest_round_data_response(monkeypatch, tmp_path):
    monkeypatch.setattr(chainlink, "LOG_DIR", tmp_path)
    monkeypatch.setattr(chainlink, "_DECIMALS_CACHE", {})

    answer = 1234567890123
    updated_at = 1778333000

    def word(value: int) -> str:
        return f"{value:064x}"

    latest_result = "0x" + "".join(
        [
            word(42),
            word(answer),
            word(updated_at - 10),
            word(updated_at),
            word(42),
        ]
    )
    decimals_result = "0x" + word(8)

    def fake_rpc(_rpc_url, _method, params, timeout=8):
        data = params[0]["data"]
        if data == chainlink.LATEST_ROUND_DATA_SELECTOR:
            return latest_result
        if data == chainlink.DECIMALS_SELECTOR:
            return decimals_result
        raise AssertionError(data)

    monkeypatch.setattr(chainlink, "_rpc_call", fake_rpc)
    price = chainlink.read_latest_price(
        "BTC",
        rpc_url="http://rpc.invalid",
        feed_address="0xc907E116054Ad103354f2D350FD2514433D57F6f",
    )

    assert price.round_id == 42
    assert price.decimals == 8
    assert price.price == answer / 10**8
    assert price.updated_at == datetime.fromtimestamp(updated_at, tz=timezone.utc)


def test_chainlink_sample_price_near_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(chainlink, "LOG_DIR", tmp_path)
    observed = datetime(2026, 5, 9, 13, 30, tzinfo=timezone.utc)
    price = chainlink.ChainlinkPrice(
        asset="BTC",
        feed_address="0xfeed",
        price=100.0,
        decimals=8,
        round_id=1,
        updated_at=observed,
        observed_at=observed + timedelta(seconds=20),
        age_seconds=20.0,
    )
    chainlink.append_sample(price)

    from direct_fastloop.signal import get_chainlink_price_near

    assert get_chainlink_price_near("BTC", observed, max_gap_seconds=30) == 100.0
    assert get_chainlink_price_near("BTC", observed - timedelta(minutes=5), max_gap_seconds=30) is None

