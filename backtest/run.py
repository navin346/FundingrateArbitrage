"""Backtest report.

  python -m backtest.fetch --days 120 --top 60     # once
  python -m backtest.run                            # baseline + out-of-sample sweep

1. Baseline: the bot's current default settings over the whole window.
2. Sweep: grid of entry/exit knobs scored on the first 2/3 of the data ONLY;
   the best few are then re-run on the untouched last 1/3 (out-of-sample).
   If a setting only wins in-sample, it is curve-fitting, not an edge."""
import itertools
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from backtest.sim import Data, Params, simulate, summarize

WARMUP_H = 72


def fmt(d):
    return (f"trades {d['trades']:>4} | win {d.get('win_pct', 0):>5}% | PnL ${d['pnl']:>8.2f} "
            f"(funding {d.get('funding', 0):>8.2f}, basis {d.get('basis', 0):>7.2f}, costs {d.get('costs', 0):>8.2f}) | "
            f"avg hold {d.get('avg_hold_h', 0):>6.1f}h | APR on capital {d['apr_pct']:>6.1f}% | "
            f"maxDD ${d.get('max_dd', 0):>7.2f} | sharpe {d.get('sharpe', 0):>5.2f}")


def main():
    D = Data()
    t0, n = WARMUP_H, D.n
    split = t0 + (n - t0) * 2 // 3
    print(f"{D.k} symbols | {D.ts(t0):%Y-%m-%d} -> {D.ts(n - 1):%Y-%m-%d} "
          f"({(n - t0) / 24:.0f} days) | train/test split at {D.ts(split):%Y-%m-%d}\n")

    base = Params()
    out = {}
    print("=== BASELINE: bot's current default settings (cost per round trip 0.26%) ===")
    for name, (a, b) in {"full": (t0, n), "train": (t0, split), "test ": (split, n)}.items():
        tr = simulate(D, base, a, b)
        out[name.strip()] = tr
        print(f"{name}: {fmt(summarize(tr, base, (b - a) / 24))}")
    tdf = pd.DataFrame(out["full"])
    if len(tdf):
        print("\nBaseline exits by reason (full window):")
        print(tdf.groupby("reason").agg(n=("pnl", "size"), pnl=("pnl", "sum"), avg_hold_h=("hold_h", "mean"),
                                         funding=("funding", "sum"), basis=("basis", "sum")).round(2).to_string())

    print("\n=== SWEEP (scored on TRAIN only) ===")
    grid = list(itertools.product([1, 24, 72], [100, 150, 250], [20, 50, 0], [2, 6, 12], [0, 24, 72], [1, 24]))
    rows = []
    for k, (se, mn, ex, ec, mh, sx) in enumerate(grid):
        p = replace(base, smooth_entry_h=se, min_entry_apr=mn, exit_apr=ex, exit_confirm=ec,
                    min_hold_h=mh, smooth_exit_h=sx)
        s = summarize(simulate(D, p, t0, split), p, (split - t0) / 24)
        rows.append((p, s))
        if k % 100 == 0:
            print(f"  {k}/{len(grid)}", file=sys.stderr)
    ok = [r for r in rows if r[1]["trades"] >= 8]
    ok.sort(key=lambda r: -r[1]["pnl"])
    pos_train = sum(r[1]["pnl"] > 0 for r in rows)
    print(f"{len(rows)} configs; {pos_train} ({pos_train / len(rows) * 100:.0f}%) profitable in-sample\n")
    print("Top 6 on TRAIN -> same settings on untouched TEST:")
    test_pnls = []
    for p, s in ok[:6]:
        st = summarize(simulate(D, p, split, n), p, (n - split) / 24)
        test_pnls.append(st["pnl"])
        print(f"  entry_smooth={p.smooth_entry_h:>2}h min_apr={p.min_entry_apr:>3.0f} exit_apr={p.exit_apr:>3.0f} "
              f"confirm={p.exit_confirm:>2} min_hold={p.min_hold_h:>2}h exit_smooth={p.smooth_exit_h:>2}h")
        print(f"     train: {fmt(s)}")
        print(f"     TEST : {fmt(st)}")
    # how do ALL configs do out-of-sample? (guards against cherry-picking the top few)
    te = [summarize(simulate(D, p, split, n), p, (n - split) / 24)["pnl"] for p, _ in ok]
    print(f"\nAll {len(ok)} configs with >=8 train trades, out-of-sample: "
          f"{sum(x > 0 for x in te)} profitable ({sum(x > 0 for x in te) / len(te) * 100:.0f}%), "
          f"median PnL ${np.median(te):.2f}, best ${max(te):.2f}, worst ${min(te):.2f}")

    # ---- chart: cumulative realized PnL, baseline vs best-on-train
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4.5))
        best = ok[0][0] if ok else base
        for label, p, c in (("bot defaults", base, "#c0362c"), ("best on train (sweep)", best, "#0b6bcb")):
            tr = pd.DataFrame(simulate(D, p, t0, n))
            if len(tr):
                tr = tr.sort_values("t_out")
                ax.step([D.ts(t) for t in tr["t_out"]], tr["pnl"].cumsum(), where="post", label=label, color=c)
        ax.axvline(D.ts(split), color="gray", ls="--", lw=1)
        ax.text(D.ts(split), ax.get_ylim()[1], "  out-of-sample →", va="top", color="gray")
        ax.axhline(0, color="black", lw=.6)
        ax.set_ylabel("cumulative realized PnL ($)")
        ax.set_title(f"Hyperliquid x Aster funding arb, ${base.notional:.0f}/leg, {D.k} symbols")
        ax.legend(loc="lower left")
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig("backtest/equity.png", dpi=130)
        print("\nchart -> backtest/equity.png")
    except ImportError:
        print("(matplotlib not installed: skipping chart)")


if __name__ == "__main__":
    main()
