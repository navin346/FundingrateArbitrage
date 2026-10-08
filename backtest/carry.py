"""Persistent-spread carry portfolio (Hyperliquid x Aster).

Idea: spikes vanish within hours (see RESULTS.md), but some pairs keep a
funding gap for weeks. Rank symbols by the trailing-window MEAN spread and by
how consistently it keeps its sign, hold the top K delta-neutral pairs, and
rebalance slowly so round-trip fees are amortised over long holds.

At each rebalance time t (only data <= t is used):
  * keep a current holding while its trailing mean is still >= keep_frac*min_apr
    in the same direction (hysteresis, avoids paying fees to churn)
  * fill free slots with the largest trailing-mean spreads that pass the
    min_apr / stability / price-match filters
Between rebalances PnL = funding actually paid + price (basis) change;
fees (rt_cost_pct, half on open, half on close) are charged when positions
change. Equal notional per pair; capital = K pairs x 2 legs x margin."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.sim import HOURS_YEAR, RT_COST_PCT


@dataclass(frozen=True)
class CarryParams:
    window_h: int = 168          # trailing window for the signal
    rebalance_h: int = 72
    top_k: int = 5
    min_apr: float = 20.0        # trailing-mean spread needed to enter, % APR
    min_stability: float = 0.0   # |mean(sign(spread))| over the window, 0..1
    keep_frac: float = 0.5       # keep a holding while trailing mean >= keep_frac*min_apr
    max_px_div: float = 1.0      # % price gap between venues at entry
    notional: float = 600.0      # per leg per pair
    lev: float = 3.0
    rt_cost_pct: float = RT_COST_PCT


_sign_cache = {}


def _stab(D, h):
    key = (id(D), h)
    if key not in _sign_cache:
        _sign_cache[key] = pd.DataFrame(np.sign(D.spread)).rolling(
            h, min_periods=int(h * 0.8)).mean().abs().values
    return _sign_cache[key]


def _mean(D, h):
    key = (id(D), "m", h)
    if key not in _sign_cache:
        _sign_cache[key] = pd.DataFrame(D.spread).rolling(h, min_periods=int(h * 0.8)).mean().values
    return _sign_cache[key]


def run_carry(D, p, t_start, t_end):
    """Returns per-period DataFrame [t, pnl, funding, basis, cost, n_pos]."""
    m, stab = _mean(D, p.window_h), _stab(D, p.window_h)
    N = p.notional
    half = N * p.rt_cost_pct / 200.0
    held = {}                                   # symbol -> +1 (short Aster/long HL) or -1
    times = list(range(t_start, t_end - 1, p.rebalance_h)) + [t_end - 1]
    rows = []
    by_sym = {}
    for i in range(len(times) - 1):
        t, t2 = times[i], times[i + 1]
        cost = 0.0
        row = m[t]
        # ---- keep / drop
        for s in list(held):
            keep = (not np.isnan(row[s]) and np.sign(row[s]) == held[s]
                    and abs(row[s]) >= p.keep_frac * p.min_apr
                    and not np.isnan(D.pxh[t, s]) and not np.isnan(D.pxa[t, s]))
            if not keep:
                del held[s]
                cost += half                    # close leg of the fee
                by_sym[s] = by_sym.get(s, 0.0) - half
        # ---- fill free slots with the best candidates
        if len(held) < p.top_k:
            g = np.abs(row)
            ok = ~np.isnan(g) & (g >= p.min_apr) & (stab[t] >= p.min_stability)
            ph, pa = D.pxh[t], D.pxa[t]
            ok &= ~np.isnan(ph) & ~np.isnan(pa)
            with np.errstate(invalid="ignore"):
                ok &= np.abs(ph - pa) / ((ph + pa) / 2) * 100 <= p.max_px_div
            for s in np.argsort(-np.nan_to_num(g)):
                if len(held) >= p.top_k:
                    break
                if ok[s] and s not in held:
                    held[s] = int(np.sign(row[s]))
                    cost += half                # open leg of the fee
                    by_sym[s] = by_sym.get(s, 0.0) - half
        # ---- earn over (t, t2]
        fund = basis = 0.0
        for s, sg in held.items():
            fa, fh = D.cfa[t2, s] - D.cfa[t, s], D.cfh[t2, s] - D.cfh[t, s]
            f_s = N * ((fa - fh) if sg > 0 else (fh - fa))
            fund += f_s
            by_sym[s] = by_sym.get(s, 0.0) + f_s
            dh, da = D.pxh[t2, s] - D.pxh[t, s], D.pxa[t2, s] - D.pxa[t, s]
            if not (np.isnan(dh) or np.isnan(da)):
                qh, qa = N / D.pxh[t, s], N / D.pxa[t, s]
                b_s = qh * (dh if sg > 0 else -dh) + qa * (-da if sg > 0 else da)
                basis += b_s
                by_sym[s] = by_sym.get(s, 0.0) + b_s
        if i == len(times) - 2:                 # close everything at the end
            cost += half * len(held)
            for s in held:
                by_sym[s] = by_sym.get(s, 0.0) - half
        rows.append({"t": t, "pnl": fund + basis - cost, "funding": fund, "basis": basis,
                     "cost": cost, "n_pos": len(held), "hours": t2 - t})
    out = pd.DataFrame(rows)
    out.attrs["by_sym"] = {D.syms[k]: v for k, v in by_sym.items()}
    return out


def stats(df, p, label=""):
    if df.empty:
        return {"pnl": 0.0, "apr_margin": 0.0, "apr_unlev": 0.0, "sharpe": 0.0, "max_dd": 0.0, "periods": 0}
    hours = df["hours"].sum()
    pnl = df["pnl"].sum()
    cap_margin = p.top_k * 2 * p.notional / p.lev
    cap_unlev = p.top_k * 2 * p.notional
    per_year = HOURS_YEAR / df["hours"].mean()
    sh = df["pnl"].mean() / df["pnl"].std() * np.sqrt(per_year) if df["pnl"].std() > 0 else 0.0
    eq = df["pnl"].cumsum()
    return {"pnl": round(pnl, 2), "funding": round(df["funding"].sum(), 2), "basis": round(df["basis"].sum(), 2),
            "costs": round(-df["cost"].sum(), 2),
            "apr_margin": round(pnl / cap_margin * HOURS_YEAR / hours * 100, 1),
            "apr_unlev": round(pnl / cap_unlev * HOURS_YEAR / hours * 100, 1),
            "sharpe": round(sh, 2), "max_dd": round((eq - eq.cummax()).min(), 2),
            "avg_pos": round(df["n_pos"].mean(), 1), "periods": len(df)}
