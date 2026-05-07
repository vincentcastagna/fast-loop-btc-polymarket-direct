from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direct_fastloop.config import load_config
from direct_fastloop.markets import discover_fast_markets
from direct_fastloop.signal import get_binance_momentum


def test_config_loads():
    cfg = load_config()
    assert cfg.asset == "BTC"
    assert cfg.max_position > 0


def test_public_imports_only():
    assert callable(discover_fast_markets)
    assert callable(get_binance_momentum)
