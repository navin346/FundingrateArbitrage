"""Add hourly MARK-price series to every data/zf/<SYM>.json (idempotent).

Trade-price candles are stale on thin Paradex markets (e.g. LDO: 2 bars in 48h),
which creates fake basis PnL. Mark prices exist every hour on both venues:
  Lighter : /api/v1/markPriceCandles      Paradex : /v1/markets/klines?price_kind=mark
Execution cost is modelled separately (measured spreads), so mark-to-mark is the
right basis for PnL.   python -m backtest.add_marks --out data/zf"""
import argparse
import json
from pathlib import Path

from backtest.fetch_zf import HOUR_MS, L, LI_BUCKET, P, PX_BUCKET, get


def lighter_mark(mid, start_s, end_s):
    out, t = [], start_s
    while end_s - t >= 3600:
        e = min(end_s, t + 490 * 3600)
        j = get(L + "/markPriceCandles", {"market_id": mid, "resolution": "1h", "start_timestamp": t,
                                           "end_timestamp": e, "count_back": 500}, LI_BUCKET)
        out += [[x["t"], float(x["c"])] for x in (j.get("c") or []) if x.get("c")]
        t = e
    return sorted({x[0]: x for x in out}.values())


def paradex_mark(sym, start_ms, end_ms):
    out, t = [], start_ms
    while t < end_ms:
        e = min(end_ms, t + 1400 * HOUR_MS)
        j = get(P + "/markets/klines", {"symbol": sym, "resolution": 60, "start_at": t, "end_at": e,
                                         "price_kind": "mark"}, PX_BUCKET)
        out += [[x[0], float(x[4])] for x in j.get("results", [])]
        t = e
    return sorted({x[0]: x for x in out}.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/zf")
    a = ap.parse_args()
    for f in sorted(Path(a.out).glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("lighter_mark") and d.get("paradex_mark"):
            continue
        start = min(d["paradex_index"][0][0], d["lighter_px"][0][0])
        end = d["paradex_index"][-1][0] + HOUR_MS
        d["lighter_mark"] = lighter_mark(d["li_id"], start // 1000, end // 1000)
        d["paradex_mark"] = paradex_mark(d["px_sym"], start, end)
        f.write_text(json.dumps(d))
        print(f"{d['sym']:8s} marks: lighter {len(d['lighter_mark'])} paradex {len(d['paradex_mark'])} "
              f"(trade candles: {len(d['lighter_px'])} / {len(d['paradex_px'])})", flush=True)


if __name__ == "__main__":
    main()
