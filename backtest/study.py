"""Is funding arbitrage worth building a system around? Walk-forward study.

  python -m backtest.fetch --days 365 --top 60 --out data/backtest365
  python -m backtest.study --data data/backtest365

Strategies compared on the SAME out-of-sample windows:
  A. spike-chaser as built      (bot defaults)
  B. spike-chaser, patched      (per-symbol cooldown, persistence, min hold, slower exit)
  C. persistent-spread carry    (backtest/carry.py), settings chosen on earlier data only

Walk-forward: 3 anchored folds. For each fold the carry settings are picked by
Sharpe on the training window and then run, untouched, on the next window.
We also report how the *typical* (median) setting did, not just the winner,
and re-run the OOS result under cheaper-fee scenarios and with a block bootstrap."""
import argparse
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

from backtest.carry import CarryParams, run_carry, stats
from backtest.sim import Data, Params, simulate, summarize

WARM = 336


def boot_ci(x, block=4, n=2000, seed=1):
    """Block-bootstrap CI for the SUM of a per-period pnl series, returned as (5%, 50%, 95%)."""
    x = np.asarray(x)
    if len(x) < block * 2:
        return (np.nan,) * 3
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(x) / block))
    sums = []
    for _ in range(n):
        idx = np.concatenate([np.arange(s, s + block) % len(x) for s in rng.integers(0, len(x), nb)])[:len(x)]
        sums.append(x[idx].sum())
    return tuple(np.percentile(sums, [5, 50, 95]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/backtest365")
    a = ap.parse_args()
    D = Data(a.data)
    t0, n = WARM, D.n
    L = n - t0
    days = lambda h: h / 24
    print(f"{D.k} symbols | {D.ts(t0):%Y-%m-%d} -> {D.ts(n - 1):%Y-%m-%d} ({days(L):.0f} days of tradable history)\n")
    folds = [(t0, t0 + int(L * .50), t0 + int(L * .667)),
             (t0, t0 + int(L * .667), t0 + int(L * .833)),
             (t0, t0 + int(L * .833), n)]
    oos_start, oos_end = folds[0][1], n
    print(f"out-of-sample span: {D.ts(oos_start):%Y-%m-%d} -> {D.ts(n - 1):%Y-%m-%d} ({days(oos_end - oos_start):.0f} days)\n")

    # ---------------- A / B: event-driven spike chasers on the OOS span ----------------
    base = Params()
    patched = replace(base, smooth_entry_h=24, cooldown_per_symbol=True, require_current_sign=True, min_hold_h=24,
                      exit_confirm=6, max_entry_apr=1000, exit_apr=0, smooth_exit_h=24, cooldown_h=24)
    print("=== A/B. Event-driven spike chasers, out-of-sample span, $600/leg ===")
    for name, p in (("A as built", base), ("B patched", patched)):
        s = summarize(simulate(D, p, oos_start, oos_end), p, days(oos_end - oos_start))
        print(f"{name:11s}: trades {s['trades']:>4} | PnL ${s['pnl']:>8.2f} (funding {s.get('funding', 0):>7.2f}, "
              f"basis {s.get('basis', 0):>7.2f}, costs {s.get('costs', 0):>8.2f}) | APR on margin {s['apr_pct']:>6.1f}% | "
              f"maxDD ${s.get('max_dd', 0):.0f}")

    # ---------------- C: carry portfolio, walk-forward ----------------
    grid = list(itertools.product([72, 168, 336], [24, 72, 168], [3, 5, 10], [10, 20, 40, 80], [0.0, 0.4, 0.7]))
    print(f"\n=== C. Persistent-spread carry: walk-forward over {len(grid)} settings x {len(folds)} folds ===")
    oos_rows, chosen, med_pnls, pos_share = [], [], [], []
    for k, (a0, a1, a2) in enumerate(folds, 1):
        res = []
        for w, r, K, th, q in grid:
            p = CarryParams(window_h=w, rebalance_h=r, top_k=K, min_apr=th, min_stability=q)
            tr = run_carry(D, p, max(a0, w), a1)
            st = stats(tr, p)
            if st["periods"] >= 8:
                res.append((st["sharpe"], st["pnl"], p))
        res.sort(key=lambda x: (-x[0], -x[1]))
        best = res[0][2]
        te = run_carry(D, best, a1, a2)
        te["fold"] = k
        oos_rows.append(te)
        chosen.append((k, best, res[0][0], stats(te, best)))
        # typical setting: every config with enough train history, run untouched on the test window
        tests = [stats(run_carry(D, p, a1, a2), p)["pnl"] for _, _, p in res]
        med_pnls.append(np.median(tests)); pos_share.append(np.mean(np.array(tests) > 0))
        print(f"fold {k}: train {D.ts(a0):%b %d}-{D.ts(a1):%b %d}, test {D.ts(a1):%b %d}-{D.ts(a2):%b %d} | "
              f"chosen window={best.window_h}h rebal={best.rebalance_h}h K={best.top_k} min_apr={best.min_apr:.0f} "
              f"stab={best.min_stability} (train sharpe {res[0][0]:.2f}) -> OOS pnl ${chosen[-1][3]['pnl']:+.2f}; "
              f"typical setting OOS median ${med_pnls[-1]:+.2f}, {pos_share[-1] * 100:.0f}% of settings profitable")
    oos = pd.concat(oos_rows)
    ref = chosen[-1][1]
    st = stats(oos, ref)
    lo, md, hi = boot_ci(oos["pnl"].values)
    ocap = ref.top_k * 2 * ref.notional / ref.lev
    print(f"\nCombined OOS ({days(oos_end - oos_start):.0f} days): net ${st['pnl']:+.2f} = funding ${st['funding']:+.2f} "
          f"+ basis ${st['basis']:+.2f} - fees ${-st['costs']:.2f}")
    print(f"  Sharpe {st['sharpe']:.2f} | max drawdown ${st['max_dd']:.2f} | periods {st['periods']} | "
          f"negative periods {np.mean(oos['pnl'] < 0) * 100:.0f}%")
    yrs = (oos_end - oos_start) / 8760
    print(f"  bootstrap 90% CI on OOS total: ${lo:+.0f} .. ${hi:+.0f}  (median ${md:+.0f}) -> "
          f"~{lo / ocap / yrs * 100:+.0f}% .. {hi / ocap / yrs * 100:+.0f}% APR on 3x margin capital (~${ocap:.0f} for K={ref.top_k})")
    print(f"  typical (median) setting out-of-sample: ${np.mean(med_pnls):+.2f} per fold; "
          f"{np.mean(pos_share) * 100:.0f}% of all settings profitable OOS")

    # ---------------- cost sensitivity on the chosen settings ----------------
    print("\n=== Fee sensitivity (same chosen settings per fold, OOS) ===")
    for c, label in ((0.26, "taker/taker both venues"), (0.18, "HL taker + zero-fee venue"),
                     (0.10, "mostly maker"), (0.05, "maker + rebates, optimistic")):
        tot, per = 0.0, []
        for (k, best, _, _), (a0, a1, a2) in zip(chosen, folds):
            p = replace(best, rt_cost_pct=c)
            tr = run_carry(D, p, a1, a2)
            tot += tr["pnl"].sum(); per.append(tr)
        allp = pd.concat(per)
        print(f"  {c:.2f}%  ({label:28s}) -> OOS net ${tot:+8.2f}  ({tot / ocap / yrs * 100:+5.1f}% APR on margin, "
              f"{tot / (ocap * ref.lev) / yrs * 100:+5.1f}% unlevered)")
    oos.to_pickle("backtest/oos_carry.pkl") if False else None


if __name__ == "__main__":
    main()
