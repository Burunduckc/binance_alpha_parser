# Binance Alpha + Futures Screener

Small script that finds Binance Alpha tokens that also have a Binance USD-M futures market and filters them by market cap.

The default cap is `$500M`. Output is saved as CSV and XLSX.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python screener.py
```

Output:

```text
binance_alpha_futures_under_500m.csv
binance_alpha_futures_under_500m.xlsx
```

The XLSX file has two sheets:

- `summary` — one row per token
- `details` — token/chain level rows

## Useful options

USDT futures only:

```bash
python screener.py --quotes USDT
```

Different market-cap limit:

```bash
python screener.py --max-market-cap 250000000
```

Save files into a separate folder:

```bash
python screener.py --out-dir output
```

Force the Binance Data Vision fallback:

```bash
python screener.py --futures-source data-vision
```

This is useful on cloud hosts where the official futures endpoint may return HTTP 451.

## Notes

Matching is done by the token symbol / futures base asset. Scaled futures contracts are handled too, for example `1000PEPEUSDT` can match `PEPE`.

This is screener that can help you with find tokens under different mc. You welcome ! 
