"""Is funding arbitrage worth building a system around? Walk-forward study.

  python -m backtest.fetch --days 365 --top 60 --out data/backtest365
  python -m backtest.study --data data/backtest365

Strategies compared on the SAME out-of-sample (OOS) windows:
  A. spike-chaser as built      (bot defaults)
  B. spike-chaser, patched      (per-symbol cooldown, persistence, min hold, slower exit)
  C. persistent-spread carry    (backtest/carry.py), settings chosen on EARLIER data only

C uses anchored walk-forward: 3 folds; per fold the settings maximising training
Sharpe are run, untouched, on the next window. Returns are always quoted on the
capital actually used by the settings chosen in each fold (K pairs x 2 legs x margin),
time-weighted across folds. Robustness: typical (median) setting, universe size,
symbol concentration, fee scenarios, block bootstrap."""
import argparse
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

from backtest.carry import CarryParams, run_carry, stats
from backtest.sim import HOURS_YEAR, Data, Params, simulate, summarize

WARM = 336
GRID = list(itertools.product([72, 168, 336], [24, 72, 168], [3, 5, 10], [10, 20, 40, 80], [0.0, 0.4, 0.7]))


def boot_ci(x, block=4, n=2000, seed=1):
    x = np.asarray(x)
    if len(x) < block * 2:
        return (np.nan,) * 3
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(x) / block))
    sums = [x[np.concatenate([np.arange(s, s + block) % len(x) for s in rng.integers(0, len(x), nb)])[:len(x)]].sum()
            for _ in range(n)]
    return tuple(np.percentile(sums, [5, 50, 95]))


def make_folds(t0, n):
    L = n - t0
    return [(t0, t0 + int(L * .50), t0 + int(L * .667)),
            (t0, t0 + int(L * .667), t0 + int(L * .833)),
            (t0, t0 + int(L * .833), n)]


def walk_forward(D, folds, cost=None):
    """Returns dict with OOS per-period frame, per-fold choices, typical-setting results, capital-time."""
    oos, chosen, med, pos, by_sym = [], [], [], [], {}
    cap_time = 0.0                                  # sum(capital * hours) in $-hours
    for k, (a0, a1, a2) in enumerate(folds, 1):
        res = []
        for w, r, K, th, q in GRID:
            p = CarryParams(window_h=w, rebalance_h=r, top_k=min(K, D.k), min_apr=th, min_stability=q)
            if cost is not None:
                p = replace(p, rt_cost_pct=cost)
            st = stats(run_carry(D, p, max(a0, w), a1), p)
            if st["periods"] >= 8:
                res.append((st["sharpe"], st["pnl"], p))
        res.sort(key=lambda x: (-x[0], -x[1]))
        best = res[0][2]
        te = run_carry(D, best, a1, a2)
        for s, v in te.attrs.get("by_sym", {}).items():
            by_sym[s] = by_sym.get(s, 0.0) + v
        te["fold"] = k
        oos.append(te)
        cap = best.top_k * 2 * best.notional / best.lev
        cap_time += cap * (a2 - a1)
        chosen.append((k, best, res[0][0], te["pnl"].sum(), cap))
        tests = [run_carry(D, p, a1, a2)["pnl"].sum() for _, _, p in res]
        med.append(float(np.median(tests)))
        pos.append(float(np.mean(np.array(tests) > 0)))
    df = pd.concat(oos)
    hours = sum(a2 - a1 for _, a1, a2 in folds)
    pnl = df["pnl"].sum()
    avg_cap = cap_time / hours
    return {"df": df, "chosen": chosen, "med": med, "pos": pos, "by_sym": by_sym, "pnl": pnl,
            "hours": hours, "avg_cap": avg_cap,
            "apr_margin": pnl / avg_cap * HOURS_YEAR / hours * 100, "folds": folds}


def report(D, wf, label):
    days = wf["hours"] / 24
    lev = wf["chosen"][0][1].lev
    print(f"\n--- {label}: {D.k} symbols ---")
    for (k, b, sh, pnl, cap), (a0, a1, a2) in zip(wf["chosen"], wf["folds"]):
        print(f"  fold {k}: test {D.ts(a1):%b %d}-{D.ts(a2):%b %d} | window={b.window_h}h rebal={b.rebalance_h}h K={b.top_k} "
              f"min_apr={b.min_apr:.0f} stab={b.min_stability} (train sharpe {sh:.2f}) -> OOS ${pnl:+.2f} on ${cap:.0f} margin"
              f" | typical setting ${wf['med'][k - 1]:+.2f}, {wf['pos'][k - 1] * 100:.0f}% of settings profitable")
    df = wf["df"]
    f, b, c = df["funding"].sum(), df["basis"].sum(), df["cost"].sum()
    lo, md, hi = boot_ci(df["pnl"].values)
    yrs = wf["hours"] / 8760
    cap = wf["avg_cap"]
    print(f"  OOS {days:.0f} days: net ${wf['pnl']:+.2f} = funding ${f:+.2f} + basis ${b:+.2f} - fees ${c:.2f}")
    print(f"  avg margin used ${cap:.0f} -> {wf['apr_margin']:+.1f}% APR on {lev:.0f}x margin | {wf['apr_margin'] / lev:+.1f}% unlevered")
    print(f"  bootstrap 90% CI: ${lo:+.0f}..${hi:+.0f} -> {lo / cap / yrs * 100:+.0f}%..{hi / cap / yrs * 100:+.0f}% APR on margin | "
          f"negative periods {np.mean(df['pnl'] < 0) * 100:.0f}% | worst period ${df['pnl'].min():.2f} | periods {len(df)}")
    tot = sum(abs(v) for v in wf["by_sym"].values())
    top = sorted(wf["by_sym"].items(), key=lambda x: -x[1])
    pos_sum = sum(v for _, v in top if v > 0)
    print(f"  concentration: top-3 symbols = {sum(v for _, v in top[:3]) / wf['pnl'] * 100 if wf['pnl'] > 0 else float('nan'):.0f}% of net "
          f"({', '.join(f'{s} ${v:+.0f}' for s, v in top[:3])}); worst 3: " + ", ".join(f"{s} ${v:+.0f}" for s, v in top[-3:]))
    return lo, md, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/backtest365")
    ap.add_argument("--chart", default="backtest/study.png")
    a = ap.parse_args()
    D = Data(a.data)
    t0, n = WARM, D.n
    folds = make_folds(t0, n)
    oos0 = folds[0][1]
    days = (n - oos0) / 24
    print(f"{D.k} symbols | tradable history {D.ts(t0):%Y-%m-%d} -> {D.ts(n - 1):%Y-%m-%d} ({(n - t0) / 24:.0f} d) | "
          f"OOS span {D.ts(oos0):%Y-%m-%d} -> {D.ts(n - 1):%Y-%m-%d} ({days:.0f} d)")

    # ---------- A / B ----------
    base = Params()
    patched = replace(base, smooth_entry_h=24, cooldown_per_symbol=True, require_current_sign=True, min_hold_h=24,
                      exit_confirm=6, max_entry_apr=1000, exit_apr=0, smooth_exit_h=24, cooldown_h=24)
    print("\n=== A/B spike-chasers, OOS span, $600/leg, 3 pairs max ($1,200 margin) ===")
    curves = {}
    for name, p in (("A as built", base), ("B patched*", patched)):
        tr = simulate(D, p, oos0, n)
        s = summarize(tr, p, days)
        t = pd.DataFrame(tr).sort_values("t_out")
        curves[name] = (t["t_out"].values, t["pnl"].cumsum().values)
        print(f"{name:11s}: trades {s['trades']:>4} | PnL ${s['pnl']:>8.2f} (funding {s.get('funding', 0):>7.2f}, basis {s.get('basis', 0):>7.2f}, "
              f"costs {s.get('costs', 0):>8.2f}) | APR on margin {s['apr_pct']:>6.1f}% | maxDD ${s.get('max_dd', 0):.0f}")
    print("* B's rules were chosen after looking at Jun-Oct 2026 data that overlaps this OOS span -> indicative only, not clean OOS")

    # ---------- C: carry, three universes ----------
    print(f"\n=== C persistent-spread carry, anchored walk-forward ({len(GRID)} settings x 3 folds) ===")
    order = np.argsort(-np.array(D.vol))
    results = {}
    for label, kk in (("full universe", D.k), ("top-20 by volume", 20), ("top-10 by volume (majors)", 10)):
        Du = D if kk >= D.k else D.subset(list(order[:kk]))
        wf = walk_forward(Du, make_folds(t0, n))
        report(Du, wf, label)
        results[label] = wf
    main_wf = results["full universe"]

    # ---------- fee scenarios (re-select under each fee, like a real operator would) ----------
    print("\n=== Fee scenarios: full re-run of walk-forward at each round-trip cost (both legs, % of notional) ===")
    for c, label in ((0.50, "pessimistic: larger size / illiquid alts"), (0.26, "taker/taker (what the bot assumed)"),
                     (0.18, "HL taker + zero-fee venue"), (0.10, "mostly maker"), (0.05, "maker + rebates (optimistic)")):
        wf = main_wf if c == 0.26 else walk_forward(D, make_folds(t0, n), cost=c)
        print(f"  {c:.2f}% {label:42s} -> OOS net ${wf['pnl']:+8.2f} | {wf['apr_margin']:+6.1f}% APR on margin | {wf['apr_margin'] / 3:+5.1f}% unlevered")

    # ---------- chart ----------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10.5, 4.6))
        for name, (tt, cc) in curves.items():
            ax.step([D.ts(int(x)) for x in tt], cc, where="post", label=name.replace("*", ""),
                    color="#c0362c" if name.startswith("A") else "#e0a030")
        c = main_wf["df"].sort_values("t")
        ax.step([D.ts(int(x)) for x in c["t"]], c["pnl"].cumsum(), where="post", label="C carry (walk-forward)", color="#0b6bcb", lw=2.2)
        ax.axhline(0, color="black", lw=.6)
        ax.set_title("Out-of-sample cumulative PnL (\\$600 per leg per pair; carry used \\$4,000 margin in fold 1, \\$1,200 after)", fontsize=10)
        ax.set_ylabel("USD"); ax.legend(loc="lower left"); fig.autofmt_xdate(); fig.tight_layout()
        fig.savefig(a.chart, dpi=130)
        print(f"\nchart -> {a.chart}")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
