"""Zero-fee venues study: Lighter (Standard) x Paradex (Retail orders). Both 0 fees for a bot.

  python -m backtest.fetch_zf --days 365 --out data/zf     # data
  python -m backtest.spreads                               # measured execution cost
  python -m backtest.study_zf

Costs are the MEASURED per-symbol round trip (cross the book to open and to close on both
venues, $600/leg) from data/zf_spreads.json, and stress-tested at x2 and x0 (all-maker ideal).
Same simulators and same walk-forward protocol as study.py (settings picked on earlier data only)."""
import argparse
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

from backtest.carry import CarryParams, run_carry, stats
from backtest.sim import HOURS_YEAR, Params, simulate, summarize
from backtest.study import boot_ci, make_folds
from backtest.zf import ZFData

WARM = 336
GRID = list(itertools.product([72, 168, 336], [24, 72, 168], [3, 5, 10], [10, 20, 40, 80], [0.0, 0.4, 0.7]))
PATCHED = dict(smooth_entry_h=24, cooldown_per_symbol=True, require_current_sign=True, min_hold_h=24,
               exit_confirm=6, max_entry_apr=1000, exit_apr=0, smooth_exit_h=24, cooldown_h=24)


def wf(D, folds, cost_mult):
    oos, chosen, med, pos = [], [], [], []
    cap_time = 0.0
    for a0, a1, a2 in folds:
        res = []
        for w, r, K, th, q in GRID:
            p = CarryParams(window_h=w, rebalance_h=r, top_k=min(K, D.k), min_apr=th, min_stability=q,
                            cost_mult=cost_mult)
            st = stats(run_carry(D, p, max(a0, w), a1), p)
            if st["periods"] >= 8:
                res.append((st["sharpe"], st["pnl"], p))
        res.sort(key=lambda x: (-x[0], -x[1]))
        best = res[0][2]
        te = run_carry(D, best, a1, a2)
        oos.append(te)
        cap = best.top_k * 2 * best.notional / best.lev
        cap_time += cap * (a2 - a1)
        chosen.append((best, res[0][0], te["pnl"].sum(), cap, te.attrs.get("by_sym", {})))
        tests = [run_carry(D, p, a1, a2)["pnl"].sum() for _, _, p in res]
        med.append(float(np.median(tests)))
        pos.append(float(np.mean(np.array(tests) > 0)))
    df = pd.concat(oos)
    hours = sum(a2 - a1 for _, a1, a2 in folds)
    return {"df": df, "chosen": chosen, "med": med, "pos": pos, "pnl": df["pnl"].sum(), "hours": hours,
            "avg_cap": cap_time / hours, "apr": df["pnl"].sum() / (cap_time / hours) * HOURS_YEAR / hours * 100}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/zf")
    ap.add_argument("--chart", default="backtest/study_zf.png")
    a = ap.parse_args()
    D = ZFData(a.data)
    t0, n = WARM, D.n
    folds = make_folds(t0, n)
    oos0 = folds[0][1]
    days = (n - oos0) / 24
    print(f"Lighter x Paradex | {D.k} symbols | {D.ts(t0):%Y-%m-%d} -> {D.ts(n - 1):%Y-%m-%d} "
          f"({(n - t0) / 24:.0f} d) | OOS {D.ts(oos0):%Y-%m-%d} -> ({days:.0f} d)")

    # ---------------- data validation ----------------
    ch = pd.DataFrame(D.checks).T
    print("\n=== Data validation (all symbols) ===")
    print(f"Lighter value/(price*rate): median {ch.lighter_value_over_rate.median():.3f} "
          f"(min {ch.lighter_value_over_rate.min():.3f}, max {ch.lighter_value_over_rate.max():.3f}) -> 1.000 = units correct")
    dev = (ch.paradex_realised_apr - ch.paradex_published_apr).abs()
    print(f"Paradex realised-from-index vs published APR: median |diff| {dev.median():.2f} pts, worst {dev.max():.2f} pts")
    with np.errstate(invalid="ignore"):
        div = np.abs(D.pxh - D.pxa) / ((D.pxh + D.pxa) / 2) * 100
    print(f"Lighter vs Paradex price gap: median {np.nanmedian(div):.3f}%, 99th pct {np.nanpercentile(div, 99):.2f}%")
    mean_gap = pd.Series(np.nanmean(D.spread[t0:], axis=0), index=D.syms)
    print("Largest average funding gaps (Paradex minus Lighter, % APR, whole period):")
    print("  " + ", ".join(f"{s} {v:+.1f}" for s, v in mean_gap.abs().sort_values(ascending=False).head(10)
                           .rename(lambda s: s).items()))

    # ---------------- costs ----------------
    print("\n=== Measured execution cost per round trip (both venues, $600/leg, % of notional) ===")
    if D.rt_cost is None:
        print("  data/zf_spreads.json missing -> run backtest.spreads first")
        return
    ci = pd.DataFrame(D.cost_info).T
    print(f"  measured for {ci.total.notna().sum()}/{D.k} symbols; unmeasured filled with 90th pct = {D.cost_fill:.3f}%")
    print(f"  median {np.median(D.rt_cost):.3f}%  | Lighter part median {ci.lighter.median():.3f}% | "
          f"Paradex part median {ci.paradex.median():.3f}%  (= ${600 * np.median(D.rt_cost) / 100:.2f} per round trip)")

    # ---------------- spike chasers ----------------
    print(f"\n=== Spike-chasers, OOS {days:.0f} days, $600/leg, 3 pairs max ($1,200 margin) ===")
    curves = {}
    for cm, cl in ((1.0, "measured cost"), (2.0, "2x cost"), (0.0, "zero cost (ideal maker)")):
        for name, p in (("A as built", Params(cost_mult=cm)), ("B patched", replace(Params(cost_mult=cm), **PATCHED))):
            tr = simulate(D, p, oos0, n)
            s = summarize(tr, p, days)
            if cm == 1.0 and tr:
                t = pd.DataFrame(tr).sort_values("t_out")
                curves[name] = (t["t_out"].values, t["pnl"].cumsum().values)
            print(f"  {name:10s} @ {cl:24s}: trades {s['trades']:>4} | net ${s['pnl']:>8.2f} (funding {s.get('funding', 0):>7.2f}, "
                  f"basis {s.get('basis', 0):>7.2f}, costs {s.get('costs', 0):>7.2f}) | APR on margin {s['apr_pct']:>7.1f}%")

    # ---------------- carry ----------------
    print(f"\n=== Persistent carry, walk-forward ({len(GRID)} settings x 3 folds) ===")
    main_wf = None
    for cm, cl in ((1.0, "measured cost"), (2.0, "2x cost"), (0.0, "zero cost")):
        w = wf(D, folds, cm)
        df = w["df"]
        lo, md, hi = boot_ci(df["pnl"].values)
        yrs = w["hours"] / 8760
        print(f"  @ {cl:13s}: net ${w['pnl']:+8.2f} = funding ${df.funding.sum():+.2f} + basis ${df.basis.sum():+.2f} "
              f"- costs ${df.cost.sum():.2f} | {w['apr']:+.1f}% APR on 3x margin ({w['apr'] / 3:+.1f}% unlevered) | "
              f"90% CI {lo / w['avg_cap'] / yrs * 100:+.0f}%..{hi / w['avg_cap'] / yrs * 100:+.0f}% | "
              f"typical setting ${np.mean(w['med']):+.2f}/fold, {np.mean(w['pos']) * 100:.0f}% of settings profitable")
        if cm == 1.0:
            main_wf = w
            for (b, sh, pnl, cap, bys), (_, a1, a2) in zip(w["chosen"], folds):
                top = sorted(bys.items(), key=lambda x: -x[1])[:3]
                print(f"     fold {D.ts(a1):%b %d}-{D.ts(a2):%b %d}: window={b.window_h}h rebal={b.rebalance_h}h K={b.top_k} "
                      f"min_apr={b.min_apr:.0f} stab={b.min_stability} -> ${pnl:+.2f} on ${cap:.0f} | top: "
                      + ", ".join(f"{s} ${v:+.1f}" for s, v in top))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10.5, 4.6))
        for name, (tt, cc) in curves.items():
            ax.step([D.ts(int(x)) for x in tt], cc, where="post", label=f"{name} (spike)",
                    color="#c0362c" if name.startswith("A") else "#e0a030")
        c = main_wf["df"].sort_values("t")
        ax.step([D.ts(int(x)) for x in c["t"]], c["pnl"].cumsum(), where="post", label="carry (walk-forward)",
                color="#0b6bcb", lw=2.2)
        ax.axhline(0, color="black", lw=.6)
        ax.set_title("Lighter x Paradex (0 fees), out-of-sample, measured execution cost, \\$600 per leg", fontsize=10)
        ax.set_ylabel("USD")
        ax.legend(loc="lower left")
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(a.chart, dpi=130)
        print(f"\nchart -> {a.chart}")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
