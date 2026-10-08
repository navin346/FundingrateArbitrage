"""Download historical funding + hourly prices for Hyperliquid x Aster into
data/backtest/<SYM>.json (git-ignored cache; re-runs only fetch what's missing).

  python -m backtest.fetch --days 120 --top 60

Universe = symbols listed on both venues, top N by the smaller of the two
venues' current 24h volume (same spirit as the bot's liquidity filter; note it
is chosen with today's volume, so it carries mild look-ahead/survivor bias)."""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402

HL = "https://api.hyperliquid.xyz/info"
AST = "https://fapi.asterdex.com"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
OUT = Path("data/backtest")
HOUR = 3600 * 1000


def _retry(fn, tries=8):
    for i in range(tries):
        try:
            r = fn()
            if r.status_code == 429:
                time.sleep(min(30, 2 ** i))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(1 + i)
    raise RuntimeError("rate limited")


def hl_funding(coin, start, end):
    out, t = [], start
    while t < end:
        j = _retry(lambda: requests.post(HL, json={"type": "fundingHistory", "coin": coin,
                                                   "startTime": t, "endTime": end}, timeout=25))
        if not j:
            break
        out += [(x["time"], float(x["fundingRate"])) for x in j]
        t = j[-1]["time"] + 1
        if len(j) < 500:
            break
    return out


def hl_px(coin, start, end):
    j = _retry(lambda: requests.post(HL, json={"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1h", "startTime": start, "endTime": end}}, timeout=25))
    return [(x["t"], float(x["c"])) for x in j]


def ast_funding(sym, start, end):
    out, t = [], start
    while t < end:
        j = _retry(lambda: requests.get(AST + "/fapi/v1/fundingRate", headers=H, timeout=25,
                                        params={"symbol": sym, "startTime": t, "limit": 1000}))
        if not j:
            break
        out += [(x["fundingTime"], float(x["fundingRate"])) for x in j]
        t = j[-1]["fundingTime"] + 1
        if len(j) < 1000:
            break
    return out


def ast_px(sym, start, end):
    out, t = [], start
    while t < end:
        j = _retry(lambda: requests.get(AST + "/fapi/v1/klines", headers=H, timeout=25,
                                        params={"symbol": sym, "interval": "1h", "startTime": t, "limit": 1000}))
        if not j:
            break
        out += [(x[0], float(x[4])) for x in j]
        t = j[-1][0] + 1
        if len(j) < 1000:
            break
    return out


def universe(top):
    df, _ = app.fetch_all()
    hl = df[(df.venue == "hyperliquid") & ~df.raw.str.contains(":")].set_index("sym")
    ast = df[df.venue == "aster"].set_index("sym")
    common = hl.index.intersection(ast.index)
    rows = []
    for s in common:
        a, b = hl.loc[s], ast.loc[s]
        if hasattr(a, "ndim") and a.ndim > 1 or hasattr(b, "ndim") and b.ndim > 1:
            continue
        rows.append({"sym": s, "hl": a["raw"], "hl_mult": float(a["mult"]),
                     "ast": b["raw"], "ast_mult": float(b["mult"]), "ast_interval_h": float(b["interval_h"]),
                     "vol": float(min(a["vol24"] or 0, b["vol24"] or 0))})
    rows.sort(key=lambda r: -r["vol"])
    return rows[:top]


def fetch_one(u, start, end):
    f = OUT / f"{u['sym']}.json"
    if f.exists():
        return u["sym"], "cached"
    try:
        d = dict(u)
        d["hl_funding"] = hl_funding(u["hl"], start, end)
        d["hl_px"] = hl_px(u["hl"], start, end)
        d["ast_funding"] = ast_funding(u["ast"], start, end)
        d["ast_px"] = ast_px(u["ast"], start, end)
        f.write_text(json.dumps(d))
        return u["sym"], f"ok ({len(d['hl_funding'])}h funding, {len(d['ast_funding'])} aster events)"
    except Exception as e:
        return u["sym"], f"FAILED {str(e)[:80]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    end = int(time.time() * 1000)
    start = end - a.days * 24 * HOUR
    uni = universe(a.top)
    print(f"{len(uni)} symbols, {a.days} days", flush=True)
    with ThreadPoolExecutor(a.workers) as pool:
        for sym, msg in pool.map(lambda u: fetch_one(u, start, end), uni):
            print(f"{sym:10s} {msg}", flush=True)


if __name__ == "__main__":
    main()
