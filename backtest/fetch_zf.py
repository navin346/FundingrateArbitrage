"""Download history for the zero-fee, bot-tradable venues: Lighter x Paradex.

  python -m backtest.fetch_zf --days 365 --out data/zf

Per symbol listed on both venues, saves data/zf/<SYM>.json with:
  lighter_funding : [ts_ms, value, rate, direction]   hourly settlements (/api/v1/fundings)
  lighter_px      : [ts_ms, close]                     1h candles (open-time stamped)
  paradex_index   : [ts_ms, funding_index, funding_rate_8h]  sampled at each hour
  paradex_px      : [ts_ms, close]                     1h klines (open-time stamped)

Ground truth for cash flows (from each venue's docs):
  Lighter : funding = -position * index_price * rate  (positive rate: longs pay). `rate` is in %.
  Paradex : accrued funding = -position * (index_now - index_then)  (USDC per unit of base)
Paradex public REST limit is 1500 req/min per IP, so index sampling is rate-limited to ~22 req/s."""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402

H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
P = "https://api.prod.paradex.trade/v1"
L = "https://mainnet.zklighter.elliot.ai/api/v1"
HOUR_MS = 3_600_000


class Bucket:
    """Simple shared rate limiter."""
    def __init__(self, per_sec):
        self.gap, self.lock, self.next = 1.0 / per_sec, threading.Lock(), time.time()

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


PX_BUCKET, LI_BUCKET = Bucket(22), Bucket(0.45)   # leave headroom for backtest.spreads


def get(url, params, bucket, tries=8):
    for i in range(tries):
        bucket.wait()
        try:
            r = requests.get(url, params=params, headers=H, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            if r.status_code == 400:            # empty trailing window: treat as no data
                return {}
            if r.status_code in (403, 418) or r.status_code >= 500:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"failed {url} {params}")


def universe():
    pm = [m for m in get(P + "/markets", {}, PX_BUCKET)["results"] if m.get("asset_kind") == "PERP"]
    lm = [m for m in get(L + "/orderBookDetails", {}, LI_BUCKET)["order_book_details"] if m.get("status") == "active"]
    ps = {app.norm_sym(m["symbol"])[0]: (m["symbol"], app.norm_sym(m["symbol"])[1]) for m in pm}
    ls = {app.norm_sym(m["symbol"])[0]: (m["symbol"], m["market_id"], app.norm_sym(m["symbol"])[1]) for m in lm}
    return [{"sym": s, "px_sym": ps[s][0], "px_mult": ps[s][1], "li_sym": ls[s][0], "li_id": ls[s][1],
             "li_mult": ls[s][2]} for s in sorted(set(ps) & set(ls))]


def lighter(u, start_s, end_s):
    fund, px = [], []
    t = start_s
    while end_s - t >= 3600:
        j = get(L + "/fundings", {"market_id": u["li_id"], "resolution": "1h", "start_timestamp": t,
                                   "end_timestamp": min(end_s, t + 740 * 3600), "count_back": 0}, LI_BUCKET)
        f = j.get("fundings") or []
        fund += [[x["timestamp"] * 1000, float(x["value"]), float(x["rate"]), x["direction"]] for x in f]
        t = (f[-1]["timestamp"] + 1) if f else t + 740 * 3600
    t = start_s
    while end_s - t >= 3600:
        e = min(end_s, t + 490 * 3600)
        j = get(L + "/candles", {"market_id": u["li_id"], "resolution": "1h", "start_timestamp": t,
                                  "end_timestamp": e, "count_back": 500}, LI_BUCKET)
        c = j.get("c") or []
        px += [[x["t"], float(x["c"])] for x in c if x.get("c")]
        t = e
    return sorted({x[0]: x for x in fund}.values()), sorted({x[0]: x for x in px}.values())


def paradex_px(u, start_ms, end_ms):
    out, t = [], start_ms
    while t < end_ms:
        e = min(end_ms, t + 1400 * HOUR_MS)
        j = get(P + "/markets/klines", {"symbol": u["px_sym"], "resolution": 60, "start_at": t, "end_at": e}, PX_BUCKET)
        out += [[x[0], float(x[4])] for x in j.get("results", [])]
        t = e
    return sorted({x[0]: x for x in out}.values())


def paradex_index_at(sym, t):
    for w in (60_000, 600_000):          # 1 min window, widen once if the oracle paused
        j = get(P + "/funding/data", {"market": sym, "start_at": t, "end_at": t + w, "page_size": 1}, PX_BUCKET)
        r = j.get("results") or []
        if r:
            x = r[0]
            return [t, float(x["funding_index"]), float(x.get("funding_rate_8h") or x.get("funding_rate") or 0)]
    return None


def paradex_index(u, hours):
    with ThreadPoolExecutor(32) as ex:
        rows = list(ex.map(lambda t: paradex_index_at(u["px_sym"], t), hours))
    return [r for r in rows if r]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--out", default="data/zf")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    end_ms = int(time.time() // 3600 * 3600 * 1000)
    start_ms = end_ms - a.days * 24 * HOUR_MS
    uni = universe()
    summ = {x["symbol"]: float(x.get("volume_24h") or 0)
            for x in get(P + "/markets/summary", {"market": "ALL"}, PX_BUCKET).get("results", [])}
    for u in uni:
        u["vol"] = summ.get(u["px_sym"], 0.0)
    uni.sort(key=lambda u: -u["vol"])                  # most liquid first
    print(f"{len(uni)} symbols on both Lighter and Paradex, {a.days} days", flush=True)
    for u in uni:
        f = out / f"{u['sym']}.json"
        if f.exists():
            print(f"{u['sym']:8s} cached", flush=True)
            continue
        t0 = time.time()
        try:
            ppx = paradex_px(u, start_ms, end_ms)
            if not ppx:
                print(f"{u['sym']:8s} no paradex history", flush=True)
                continue
            first = max(start_ms, ppx[0][0])                 # skip hours before listing
            hours = list(range(first, end_ms + 1, HOUR_MS))
            lf, lpx = lighter(u, first // 1000, end_ms // 1000)
            pidx = paradex_index(u, hours)
            d = dict(u, lighter_funding=lf, lighter_px=lpx, paradex_index=pidx, paradex_px=ppx)
            f.write_text(json.dumps(d))
            print(f"{u['sym']:8s} ok | {len(hours)}h | lighter funding {len(lf)} px {len(lpx)} | "
                  f"paradex index {len(pidx)} px {len(ppx)} | {time.time() - t0:.0f}s", flush=True)
        except Exception as e:
            print(f"{u['sym']:8s} FAILED {str(e)[:120]}", flush=True)


if __name__ == "__main__":
    main()
