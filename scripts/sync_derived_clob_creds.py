from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from direct_fastloop.clob_exec import DirectClob
from direct_fastloop.config import ENV_PATH, load_dotenv, load_wallet_config


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-6:]}"


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [key for key in updates if key not in seen]
    if missing and out and out[-1].strip():
        out.append("")
    for key in missing:
        out.append(f"{key}={updates[key]}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    load_dotenv()
    wallet = load_wallet_config()
    if not wallet.private_key:
        print("POLY_PRIVATE_KEY is missing in .env")
        return 2

    creds = DirectClob.derive_api_creds(wallet)
    updates = {
        "POLY_API_KEY": creds.api_key,
        "POLY_API_SECRET": creds.api_secret,
        "POLY_API_PASSPHRASE": creds.api_passphrase,
    }

    backup = ENV_PATH.with_name(f".env.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    if ENV_PATH.exists():
        shutil.copy2(ENV_PATH, backup)
    upsert_env(ENV_PATH, updates)
    print(f"Updated .env with derived CLOB creds for current private key.")
    print(f"POLY_API_KEY={mask(creds.api_key)}")
    if backup.exists():
        print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
