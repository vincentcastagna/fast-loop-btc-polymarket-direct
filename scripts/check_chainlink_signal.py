from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from direct_fastloop.chainlink import configured_feed_address, configured_rpc_url, read_latest_price
from direct_fastloop.config import load_config, load_dotenv


def main() -> None:
    load_dotenv()
    config = load_config()
    rpc_url = configured_rpc_url(config.chainlink_rpc_url)
    feed_address = configured_feed_address(config.asset, config.chainlink_feed_address)
    price = read_latest_price(config.asset, rpc_url=rpc_url, feed_address=feed_address)
    print(
        json.dumps(
            {
                "asset": config.asset,
                "signal_source": config.signal_source,
                "chainlink_enabled": config.chainlink_enabled,
                "rpc_url": rpc_url,
                "feed_address": price.feed_address,
                "configured_feed_address": feed_address,
                "price": price.price,
                "decimals": price.decimals,
                "round_id": price.round_id,
                "updated_at_utc": price.updated_at.isoformat(),
                "observed_at_utc": price.observed_at.isoformat(),
                "age_seconds": round(price.age_seconds, 3),
                "fresh_enough": price.age_seconds <= config.chainlink_max_feed_age_seconds,
                "max_feed_age_seconds": config.chainlink_max_feed_age_seconds,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
