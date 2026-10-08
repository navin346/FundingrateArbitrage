"""Measure real execution cost of a $NOTIONAL round trip on Lighter and Paradex.

  python -m backtest.spreads --rounds 12 --every 600 --notional 600

Lighter: full public book (orderBookOrders) -> VWAP to buy and to sell NOTIONAL.
Paradex: two views
  * public book (no retail-only RPI quotes) -> conservative VWAP
  * interactive BBO (includes RPI, what a Retail order sees); used only when the
    best level is big enough to fill NOTIONAL, else fall back to the public book.
Round-trip cost on a venue = (buy_vwap - sell_vwap) / mid: you buy once and sell once
(open + close) crossing the book each time. Both venues have 0 fees for this flow.
Writes data/zf_spreads.json with every snapshot and per-symbol medians."""
import argparse
import json
import statistics as st
import time
from pathlib import Path

import requests

from backtest.fetch_zf import H, L, P, Bucket

LB, PB = Bucket(0.5), Bucket(1.5)


def j(url, params, bucket):
    bucket.wait()
    r = requests.get(url, params=params, headers=H, timeout=20)
    return r.json() if r.ok else {}


def vwap(levels, notional):
    """levels: [(price, size)] best-first. Returns VWAP to fill notional, or None if book too thin."""
    got_q = got_n = 0.0
    for px, sz in levels:
        take = min(sz, (notional - got_n) / px)
        got_q += take
        got_n += take * px
        if got_n >= notional * 0.9999:
            return got_n / got_q
    return None


def lighter_cost(mid_id, notional):
    b = j(L + "/orderBookOrders", {"market_id": mid_id, "limit": 100}, LB)
    asks = [(float(o["price"]), float(o["remaining_base_amount"])) for o in b.get("asks", [])]
    bids = [(float(o["price"]), float(o["remaining_base_amount"])) for o in b.get("bids", [])]
    if not asks or not bids:
        return None
    mid = (asks[0][0] + bids[0][0]) / 2
    a, bb = vwap(asks, notional), vwap(bids, notional)
    return None if a is None or bb is None else (a - bb) / mid * 100


def paradex_cost(sym, notional):
    b = j(P + f"/orderbook/{sym}", {"depth": 100}, PB)
    asks = [(float(p), float(s)) for p, s in b.get("asks", [])]
    bids = [(float(p), float(s)) for p, s in b.get("bids", [])]
    if not asks or not bids:
        return None, None
    mid = (asks[0][0] + bids[0][0]) / 2
    a, bb = vwap(asks, notional), vwap(bids, notional)
    public = None if a is None or bb is None else (a - bb) / mid * 100
    q = j(P + f"/bbo/{sym}/interactive", {}, PB)
    retail = public
    try:
        ia, ib = float(q["ask"]), float(q["bid"])
        ias, ibs = float(q["ask_size"]) * ia, float(q["bid_size"]) * ib
        ra = ia if ias >= notional else a          # top level big enough? else walk public book
        rb = ib if ibs >= notional else bb
        if ra is not None and rb is not None:
            retail = (ra - rb) / ((ia + ib) / 2) * 100
    except (KeyError, ValueError, TypeError):
        pass
    return public, retail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--every", type=int, default=600)
    ap.add_argument("--notional", type=float, default=600)
    ap.add_argument("--universe", default="data/zf")
    a = ap.parse_args()
    from backtest.fetch_zf import universe
    uni = universe()
    snaps = {u["sym"]: {"lighter": [], "paradex_public": [], "paradex_retail": []} for u in uni}
    for r in range(a.rounds):
        t0 = time.time()
        for u in uni:
            s = snaps[u["sym"]]
            try:
                s["lighter"].append(lighter_cost(u["li_id"], a.notional))
                pub, ret = paradex_cost(u["px_sym"], a.notional)
                s["paradex_public"].append(pub)
                s["paradex_retail"].append(ret)
            except Exception as e:
                print("err", u["sym"], str(e)[:80], flush=True)
        print(f"round {r + 1}/{a.rounds} done in {time.time() - t0:.0f}s", flush=True)
        if r + 1 < a.rounds:
            time.sleep(max(0, a.every - (time.time() - t0)))

    def med(xs):
        xs = [x for x in xs if x is not None]
        return st.median(xs) if len(xs) >= max(2, a.rounds // 2) else None
    out = {"notional": a.notional, "rounds": a.rounds, "snapshots": snaps,
           "median_rt_cost_pct": {s: {k: med(v) for k, v in d.items()} for s, d in snaps.items()}}
    Path("data").mkdir(exist_ok=True)
    Path("data/zf_spreads.json").write_text(json.dumps(out))
    print("saved data/zf_spreads.json", flush=True)


if __name__ == "__main__":
    main()
