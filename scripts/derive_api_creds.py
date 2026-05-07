from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from direct_fastloop.clob_exec import DirectClob
from direct_fastloop.config import load_dotenv, load_wallet_config


def main() -> int:
    load_dotenv()
    wallet = load_wallet_config()
    if not wallet.private_key:
        print("POLY_PRIVATE_KEY is required. Put it in .env or your shell env first.")
        return 2
    creds = DirectClob.derive_api_creds(wallet)
    print("Add these to .env. Treat them like secrets:")
    print(f"POLY_API_KEY={creds.api_key}")
    print(f"POLY_API_SECRET={creds.api_secret}")
    print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

