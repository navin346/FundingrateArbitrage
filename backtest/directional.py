"""Directional hypothesis tests (momentum, positioning, pump-following) vs a coin-flip control.

  python -m backtest.directional --data data/backtest365

Each day (non-overlapping 24h steps), each rule picks assets and a side. Trades use a
symmetric stop/target on the HOURLY path (default 2%/2%), otherwise exit after 24h;
round-trip cost 0.10%. Reports win rate, expectancy, half-split stability and the equity
multiple at 3x/5x. A rule is only interesting if it beats the coin-flip control out of sample."""
import argparse

import numpy as np
import pandas as pd

from backtest.sim import Data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/backtest365")
    ap.add_argument("--stop", type=float, default=2.0)
    ap.add_argument("--target", type=float, default=2.0)
    ap.add_argument("--cost", type=float, default=0.10)
    a = ap.parse_args()
    D = Data(a.data)
    H, n, t0 = 24, D.n, 24 * 8
    px = pd.DataFrame(D.pxh, columns=D.syms).ffill(limit=3)
    fund = pd.DataFrame(D.hl_apr, columns=D.syms).rolling(24, min_periods=12).mean()
    lp = np.log(px)
    days = list(range(t0, n - H - 1, H))
    col = px.columns.get_loc

    def trade(i, t, side):
        p = px.iloc[t:t + H + 1, i].values
        if np.isnan(p[0]) or np.isnan(p).mean() > 0.2:
            return None
        path = (p / p[0] - 1) * 100 * side
        for x in path[1:]:
            if np.isnan(x):
                continue
            if x <= -a.stop:
                return -a.stop - a.cost
            if x >= a.target:
                return a.target - a.cost
        return path[~np.isnan(path)][-1] - a.cost

    def top_bottom(series):
        s = series.dropna()
        return [(col(x), 1) for x in s.nlargest(2).index] + [(col(x), -1) for x in s.nsmallest(2).index]

    rng = np.random.default_rng(0)
    rules = {f"Momentum {k}d: long top-2 trend, short bottom-2": (lambda t, k=k: top_bottom(lp.iloc[t] - lp.iloc[t - 24 * k]))
             for k in (3, 5, 7)}
    rules["Positioning: long most-long-crowded, short most-short-crowded"] = lambda t: top_bottom(fund.iloc[t])

    def pump(t):
        r = (lp.iloc[t] - lp.iloc[t - 24]).dropna() * 100
        return [(col(s), 1) for s in r[r > 10].index] + [(col(s), -1) for s in r[r < -10].index]
    rules["Follow the pumper: after a >10% day, trade in its direction"] = pump
    rules["CONTROL: coin flip (random asset, random side)"] = lambda t: [
        (int(i), int(rng.choice([-1, 1]))) for i in rng.choice(len(D.syms), 4, replace=False)]

    half = days[len(days) // 2]
    print(f"{D.k} assets, {len(days)} days ({D.ts(days[0]):%b %Y} - {D.ts(days[-1]):%b %Y}); "
          f"stop {a.stop}% / target {a.target}% / else 24h exit; cost {a.cost}%")
    print(f"{'rule':62s} {'trades':>6} {'win%':>6} {'avg/trade':>9} {'win% H1':>8} {'win% H2':>8} {'equity x @3x':>12} {'@5x':>6} {'maxDD @5x':>9}")
    for name, pick in rules.items():
        res = [(t, r) for t in days for i, side in pick(t) for r in [trade(i, t, side)] if r is not None]
        df = pd.DataFrame(res, columns=["t", "r"])
        daily = df.groupby("t").r.mean() / 100
        e5 = np.cumprod(1 + 5 * daily)
        print(f"{name:62s} {len(df):>6} {(df.r > 0).mean() * 100:>6.1f} {df.r.mean():>+8.2f}% "
              f"{(df[df.t < half].r > 0).mean() * 100:>8.1f} {(df[df.t >= half].r > 0).mean() * 100:>8.1f} "
              f"{np.prod(1 + 3 * daily):>12.2f} {e5.iloc[-1]:>6.2f} {(e5 / e5.cummax() - 1).min() * 100:>8.0f}%")


if __name__ == "__main__":
    main()
