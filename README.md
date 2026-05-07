# Polymarket FastLoop Direct

Direct Polymarket CLOB version of the BTC 5-minute FastLoop bot.

This project is intentionally separate from the Simmer bot. It does not import
`simmer-sdk`, does not touch the Simmer managed wallet, and does not modify the
existing `C:\Dev\TradeBtc\polymarket-fast-loop` repo.

## Current Scope

- Public market discovery from Polymarket Gamma.
- Public orderbook reads from `https://clob.polymarket.com`.
- Binance BTC signal logic ported from the working FastLoop bot.
- Parity test against `C:\Dev\TradeBtc\polymarket-fast-loop\fastloop_trader.py`
  for signal score, path-aware probability, setup score, fees, and maker bid math.
- Dry-run taker and maker candidate evaluation.
- Live taker/maker one-shot execution through `py-clob-client-v2`, blocked by
  explicit wallet secrets and live confirmation flags.
- Scheduled direct live taker runner with local safety caps.
- Shadow logs for late-entry and exit-snapshot research.
- Local logs under `logs/`.

## What You Still Need To Set Up

You cannot trade directly from the Simmer managed wallet. For direct trading,
you need a separate Polygon/Polymarket wallet controlled by you.

1. Create or choose a dedicated Polygon wallet for this bot.
2. Fund it with a tiny test amount only.
3. Make sure it is ready for Polymarket V2 collateral and approvals.
4. Put the wallet private key in `.env` as `POLY_PRIVATE_KEY`.
5. Generate CLOB API credentials:

```powershell
cd C:\Dev\TradeBtc\polymarket-fast-loop-direct
python scripts\derive_api_creds.py
```

6. Paste the generated `POLY_API_KEY`, `POLY_API_SECRET`, and
   `POLY_API_PASSPHRASE` into `.env`.

For proxy/safe wallets, also set:

```text
POLY_SIGNATURE_TYPE=2
POLY_FUNDER_ADDRESS=0x...
```

For a normal EOA wallet, leave those blank.

## Install

```powershell
cd C:\Dev\TradeBtc\polymarket-fast-loop-direct
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

## Dry Runs

Taker dry-run:

```powershell
python -m direct_fastloop.main --mode taker
```

Maker dry-run:

```powershell
python -m direct_fastloop.main --mode maker
```

Status check after wallet setup:

```powershell
python -m direct_fastloop.main --status
```

## Live One-Shot

Live is intentionally gated. The command below runs one cycle only:

```powershell
$env:DIRECT_LIVE_CONFIRM="YES"
python -m direct_fastloop.main --live --yes-i-understand --mode taker
```

For maker one-shot with automatic wait/cancel:

```powershell
$env:DIRECT_LIVE_CONFIRM="YES"
python -m direct_fastloop.main --live --yes-i-understand --mode maker --wait-cancel 60
```

## Strategy And Safety Defaults

`config.json` uses the same conservative strategy parameters as the current
SIM bot, with direct-live safety overlays:

- YES-only core strategy.
- A tiny choppy-YES exception, capped separately, for strict continuation setups.
- Max `$5` strategy size.
- Strategy window `45s` to `150s` remaining.
- Taker order type `FAK`.

Direct live execution has separate safety caps:

- Max `5` direct live trades/day.
- Direct live daily budget `$25`.
- Max `2` resolved BTC losses/day.
- Daily cash stop at `-$10`.
- Choppy-YES exception capped at `1` live success/day.
- NO live micro-test disabled by default.
- Live still requires `.env`, `--live`, `--yes-i-understand`, and
  `DIRECT_LIVE_CONFIRM=YES`.

The bot also writes research-only shadow observations to
`logs/direct_shadow_decisions.jsonl`. These observations never place orders;
they are used to analyze possible late entries and exit logic later:

```powershell
python analyze_direct_shadow.py --hours 24
```

Run the parity check after strategy edits:

```powershell
python tests\parity_against_current_bot.py
```

## Why This Exists

The Simmer SDK managed-wallet path is currently blocked by upstream Polymarket
V2 auth failures and Simmer ledger artifacts. This direct project gives us a
separate execution path and a clearer source of truth:

- CLOB open orders.
- CLOB trades.
- Wallet balance/allowance.
- Local JSONL logs.

## Notes

Official Polymarket docs say CLOB trading uses two auth levels: L1 private-key
signing to create/derive API credentials, then L2 HMAC credentials for trading
requests. Orders still require local EIP-712 signing.
