#!/usr/bin/env python3
"""
Find tokens with market cap below a threshold that are present on both:
1) Binance Alpha
2) Binance USDⓈ-M Futures

Outputs:
- binance_alpha_futures_under_500m.xlsx  (details + ticker_summary sheets)
- binance_alpha_futures_under_500m.csv   (details)

Install:
  pip install requests pandas openpyxl
Run:
  python binance_alpha_futures_under_500m.py
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

ALPHA_TOKEN_LIST_URL = (
    "https://www.binance.com/bapi/defi/v1/public/"
    "wallet-direct/buw/wallet/cex/alpha/all/token/list"
)
ALPHA_EXCHANGE_INFO_URL = (
    "https://www.binance.com/bapi/defi/v1/public/alpha-trade/get-exchange-info"
)
FUTURES_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

# Binance often names futures contracts with scaled base assets, e.g. 1000PEPEUSDT.
SCALE_PREFIXES = ("1000000", "100000", "10000", "1000", "100")


def fetch_json(session: requests.Session, url: str) -> Any:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def binance_data(payload: Any, label: str) -> Any:
    """Return payload['data'] for bapi responses, with a clear error if Binance says no."""
    if isinstance(payload, dict) and "success" in payload:
        if not payload.get("success"):
            raise RuntimeError(f"{label} failed: {payload}")
        return payload.get("data")
    return payload


def to_float(x: Any) -> float | None:
    if x in (None, "", "null"):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def clean_symbol(s: Any) -> str:
    return str(s or "").strip().upper()


def normalize_futures_base(base_asset: str) -> set[str]:
    """
    Return possible token symbols for a futures base asset.
    Examples:
      PEPE -> {PEPE}
      1000PEPE -> {1000PEPE, PEPE}
    """
    base = clean_symbol(base_asset)
    out = {base} if base else set()
    for prefix in SCALE_PREFIXES:
        if base.startswith(prefix) and len(base) > len(prefix):
            out.add(base[len(prefix):])
    return out


def fmt_dt_ms(ms: Any) -> str:
    try:
        ms_int = int(ms)
        if ms_int <= 0:
            return ""
        return datetime.fromtimestamp(ms_int / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def unique_join(values: list[Any]) -> str:
    cleaned = []
    for v in values:
        if v is None:
            continue
        text = str(v).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return ",".join(cleaned)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-market-cap", type=float, default=500_000_000)
    parser.add_argument(
        "--quotes",
        default="USDT,USDC",
        help="Futures quote assets to include, comma-separated. Use USDT for USDT-only perps.",
    )
    parser.add_argument(
        "--keep-alpha-not-trading",
        action="store_true",
        help="Keep Alpha token-list items even if not present in Alpha exchangeInfo/TRADING symbols.",
    )
    parser.add_argument("--out-prefix", default="binance_alpha_futures_under_500m")
    args = parser.parse_args()

    quote_assets = {q.strip().upper() for q in args.quotes.split(",") if q.strip()}

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 alpha-futures-screener/1.0",
            "Accept": "application/json,text/plain,*/*",
        }
    )

    alpha_tokens_payload = fetch_json(session, ALPHA_TOKEN_LIST_URL)
    alpha_exchange_payload = fetch_json(session, ALPHA_EXCHANGE_INFO_URL)
    futures_payload = fetch_json(session, FUTURES_EXCHANGE_INFO_URL)

    alpha_tokens = binance_data(alpha_tokens_payload, "Alpha token list") or []
    alpha_exchange = binance_data(alpha_exchange_payload, "Alpha exchange info") or {}
    alpha_symbols = alpha_exchange.get("symbols", []) if isinstance(alpha_exchange, dict) else []
    futures_symbols = futures_payload.get("symbols", []) if isinstance(futures_payload, dict) else []

    # Alpha pairs that are actually trading, e.g. ALPHA_175USDT.
    alpha_trading_base_assets = {
        clean_symbol(s.get("baseAsset"))
        for s in alpha_symbols
        if clean_symbol(s.get("status")) == "TRADING"
        and clean_symbol(s.get("quoteAsset")) == "USDT"
    }

    # Build futures lookup by normalized base asset.
    futures_by_token: dict[str, list[dict[str, Any]]] = {}
    for s in futures_symbols:
        if clean_symbol(s.get("status")) != "TRADING":
            continue
        if clean_symbol(s.get("contractType")) != "PERPETUAL":
            continue
        if clean_symbol(s.get("quoteAsset")) not in quote_assets:
            continue

        base = clean_symbol(s.get("baseAsset"))
        enriched = {
            "futures_symbol": clean_symbol(s.get("symbol")),
            "futures_base_asset": base,
            "futures_quote_asset": clean_symbol(s.get("quoteAsset")),
            "futures_pair": clean_symbol(s.get("pair")),
            "futures_onboard_date": fmt_dt_ms(s.get("onboardDate")),
        }
        for token_key in normalize_futures_base(base):
            futures_by_token.setdefault(token_key, []).append(enriched)

    rows: list[dict[str, Any]] = []
    skipped_no_mcap = 0
    skipped_not_trading_alpha = 0

    for t in alpha_tokens:
        symbol = clean_symbol(t.get("symbol"))
        if not symbol:
            continue

        alpha_id = clean_symbol(t.get("alphaId"))
        if not args.keep_alpha_not_trading and alpha_id and alpha_id not in alpha_trading_base_assets:
            skipped_not_trading_alpha += 1
            continue

        market_cap = to_float(t.get("marketCap"))
        if market_cap is None:
            skipped_no_mcap += 1
            continue
        if market_cap >= args.max_market_cap:
            continue

        futures_matches = futures_by_token.get(symbol, [])
        if not futures_matches:
            continue

        exact = any(m["futures_base_asset"] == symbol for m in futures_matches)
        match_type = "exact" if exact else "scaled_futures_base_asset"

        rows.append(
            {
                "symbol": symbol,
                "name": t.get("name", ""),
                "market_cap_usd": market_cap,
                "fdv_usd": to_float(t.get("fdv")),
                "price_usd": to_float(t.get("price")),
                "change_24h_pct": to_float(t.get("percentChange24h")),
                "volume_24h_usd": to_float(t.get("volume24h")),
                "liquidity_usd": to_float(t.get("liquidity")),
                "holders": to_float(t.get("holders")),
                "chain_id": t.get("chainId", ""),
                "chain": t.get("chainName", ""),
                "contract_address": t.get("contractAddress", ""),
                "alpha_id": alpha_id,
                "alpha_pair": f"{alpha_id}USDT" if alpha_id else "",
                "alpha_listing_time_utc": fmt_dt_ms(t.get("listingTime")),
                "alpha_offline": t.get("offline"),
                "alpha_listing_cex": t.get("listingCex"),
                "futures_symbols": unique_join([m["futures_symbol"] for m in futures_matches]),
                "futures_base_assets": unique_join([m["futures_base_asset"] for m in futures_matches]),
                "futures_quote_assets": unique_join([m["futures_quote_asset"] for m in futures_matches]),
                "futures_onboard_dates_utc": unique_join([m["futures_onboard_date"] for m in futures_matches]),
                "match_type": match_type,
                "source_refreshed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        print("No matches found. Try --keep-alpha-not-trading or --quotes USDT,USDC.")
        print(f"Skipped Alpha rows without market cap: {skipped_no_mcap}")
        print(f"Skipped Alpha rows not trading on Alpha exchangeInfo: {skipped_not_trading_alpha}")
        return 0

    df = df.sort_values(["market_cap_usd", "symbol"], ascending=[False, True]).reset_index(drop=True)

    # One row per ticker summary. Details sheet keeps every chain/contract row.
    summary = (
        df.groupby("symbol", as_index=False)
        .agg(
            name=("name", "first"),
            market_cap_usd=("market_cap_usd", "max"),
            price_usd=("price_usd", "first"),
            change_24h_pct=("change_24h_pct", "first"),
            volume_24h_usd=("volume_24h_usd", "sum"),
            liquidity_usd=("liquidity_usd", "sum"),
            chains=("chain", lambda s: unique_join(list(s))),
            alpha_ids=("alpha_id", lambda s: unique_join(list(s))),
            alpha_pairs=("alpha_pair", lambda s: unique_join(list(s))),
            futures_symbols=("futures_symbols", lambda s: unique_join(",".join(s).split(","))),
            futures_base_assets=("futures_base_assets", lambda s: unique_join(",".join(s).split(","))),
            match_type=("match_type", lambda s: unique_join(list(s))),
        )
        .sort_values(["market_cap_usd", "symbol"], ascending=[False, True])
        .reset_index(drop=True)
    )

    csv_path = f"{args.out_prefix}.csv"
    xlsx_path = f"{args.out_prefix}.xlsx"
    df.to_csv(csv_path, index=False)

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="ticker_summary")
        df.to_excel(writer, index=False, sheet_name="details")

    print(f"Found {len(summary)} tickers / {len(df)} Alpha rows")
    print(f"Saved: {xlsx_path}")
    print(f"Saved: {csv_path}")
    print()
    print(summary.head(50).to_string(index=False))
    print()
    print(f"Skipped Alpha rows without market cap: {skipped_no_mcap}")
    print(f"Skipped Alpha rows not trading on Alpha exchangeInfo: {skipped_not_trading_alpha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
