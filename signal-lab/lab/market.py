"""Hyperliquid public market data (no keys)."""
import time

import requests

HL = "https://api.hyperliquid.xyz/info"


def _post(payload, tries=4):
    for i in range(tries):
        try:
            r = requests.post(HL, json=payload, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def contexts(coins):
    """Current mark price, hourly funding rate and open interest for each coin."""
    out = {}
    for dex in (None, "xyz"):
        payload = {"type": "metaAndAssetCtxs"}
        if dex:
            payload["dex"] = dex
        meta, ctxs = _post(payload)
        for u, c in zip(meta["universe"], ctxs):
            if u["name"] in coins:
                out[u["name"]] = {"mark": float(c["markPx"]), "funding": float(c["funding"]),
                                  "oi": float(c["openInterest"]) * float(c["markPx"])}
    return out


def candles(coin, hours):
    """Hourly candles for the last `hours` hours: list of dicts t,o,h,l,c (t = open time, ms)."""
    end = int(time.time() * 1000)
    j = _post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h",
                                                 "startTime": end - hours * 3_600_000, "endTime": end}})
    return [{"t": x["t"], "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]), "c": float(x["c"])}
            for x in j]


def funding_history(coin, hours):
    end = int(time.time() * 1000)
    j = _post({"type": "fundingHistory", "coin": coin, "startTime": end - hours * 3_600_000})
    return [(x["time"], float(x["fundingRate"])) for x in j]
