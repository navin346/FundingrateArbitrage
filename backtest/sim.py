"""Hourly backtest of the Setu bot's strategy on Hyperliquid x Aster.

Mirrors bot/engine.py rules and the radar's cost model (build_pairs):
  entry  : best |funding spread| passing APR / breakeven / px-divergence /
           cooldown / capacity filters, one new pair per tick
  exits  : stop-loss, px divergence, max hold, spread collapse (N confirms)
  breaker: daily realized loss limit halts entries

Differences from live, on purpose (so the result is not flattered):
  * funding PnL uses the funding that was *actually paid* each hour (HL) /
    at each settlement (Aster), not the bot's estimate from the spread
  * signals at hour t only use rates already published at t
  * fills at the hourly close; fees+spread charged via the same rt-cost model
Known limits: 1h tick (live ticked every 5 min but Actions ran every ~2-5 h),
no liquidity/OI history (universe = top-N by today's volume), no liquidation
model (the $-stop-loss triggers long before 3x liquidation on a hedged pair).
"""
import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

HOUR = 3600 * 1000
HOURS_YEAR = 8760.0
FEE_BPS = {"hl": 4.5, "ast": 4.0}     # taker, per side (app.DEFAULT_FEES)
SLIP_BPS = {"hl": 3.0, "ast": 6.0}    # app.DEFAULT_SLIP
RT_COST_PCT = sum((2 * FEE_BPS[v] + SLIP_BPS[v]) / 100 for v in ("hl", "ast"))  # 0.26%


@dataclass(frozen=True)
class Params:
    """Defaults = bot/config.py defaults."""
    min_entry_apr: float = 100.0
    max_entry_apr: float = 3000.0
    max_breakeven_days: float = 4.0
    max_px_div_entry: float = 1.0
    max_px_div_exit: float = 3.0
    exit_apr: float = 20.0
    exit_confirm: int = 2
    max_hold_days: float = 10.0
    stop_loss: float = 100.0
    cooldown_h: float = 6.0
    max_pairs: int = 3
    margin: float = 200.0
    lev: float = 3.0
    daily_loss_limit: float = 200.0
    halt_h: float = 24.0
    # experiment knobs (bot defaults = no smoothing, no min hold)
    smooth_entry_h: int = 1
    smooth_exit_h: int = 1
    min_hold_h: int = 0

    @property
    def notional(self):
        return self.margin * self.lev


class Data:
    def __init__(self, folder="data/backtest"):
        files = sorted(Path(folder).glob("*.json"))
        raw = [json.loads(f.read_text()) for f in files]
        raw = [r for r in raw if r["hl_funding"] and r["ast_funding"] and r["hl_px"] and r["ast_px"]]
        self.syms = [r["sym"] for r in raw]
        lo = min(min(t for t, _ in r["hl_funding"]) for r in raw) // HOUR
        hi = max(max(t for t, _ in r["hl_funding"]) for r in raw) // HOUR
        self.t0_ms = lo * HOUR
        n, k = hi - lo + 1, len(raw)
        nan = np.full((n, k), np.nan)
        self.pxh, self.pxa = nan.copy(), nan.copy()
        hl_apr, ast_apr = nan.copy(), nan.copy()
        self.fh, self.fa = np.zeros((n, k)), np.zeros((n, k))   # cash-flow rates actually paid
        for j, r in enumerate(raw):
            for t, x in r["hl_funding"]:
                i = t // HOUR - lo
                if 0 <= i < n:
                    hl_apr[i, j] = x * HOURS_YEAR * 100
                    self.fh[i, j] += x
            ev = sorted(r["ast_funding"])
            prev_t, default_h = None, r["ast_interval_h"]
            for t, x in ev:
                i = t // HOUR - lo
                ih = (t - prev_t) / HOUR if prev_t else default_h
                prev_t = t
                if 0 <= i < n:
                    ast_apr[i, j] = x * (HOURS_YEAR / max(ih, 1.0)) * 100  # last settled, per-interval
                    self.fa[i, j] += x
            for t, c in r["hl_px"]:
                i = t // HOUR - lo + 1                      # close of candle opening at t
                if 0 <= i < n:
                    self.pxh[i, j] = c / r["hl_mult"]
            for t, c in r["ast_px"]:
                i = t // HOUR - lo + 1
                if 0 <= i < n:
                    self.pxa[i, j] = c / r["ast_mult"]
        df = pd.DataFrame
        self.hl_apr = df(hl_apr).ffill(limit=3).values
        self.ast_apr = df(ast_apr).ffill(limit=12).values       # forward fill = "last funding rate"
        self.pxh = df(self.pxh).ffill(limit=3).values
        self.pxa = df(self.pxa).ffill(limit=3).values
        self.spread = self.ast_apr - self.hl_apr                 # >0: short aster / long HL
        self.cfh, self.cfa = np.cumsum(self.fh, 0), np.cumsum(self.fa, 0)
        self.n, self.k = n, k
        self._roll = {}

    def smoothed(self, h):
        if h <= 1:
            return self.spread
        if h not in self._roll:
            self._roll[h] = pd.DataFrame(self.spread).rolling(h, min_periods=h).mean().values
        return self._roll[h]

    def ts(self, i):
        return pd.Timestamp(self.t0_ms + i * HOUR, unit="ms")


def simulate(D, p, t_start, t_end):
    """Run hours [t_start, t_end). Returns list of trade dicts (all closed; leftovers marked at end)."""
    ent, ext = D.smoothed(p.smooth_entry_h), D.smoothed(p.smooth_exit_h)
    cost_usd = p.notional * RT_COST_PCT / 100
    open_, cool, trades, closes = {}, {}, [], []
    halt_until = -1

    def close(s, t, reason):
        pos = open_.pop(s)
        pnl = pos["basis"](t) + pos["fund"](t) - cost_usd
        trades.append({"sym": D.syms[s], "t_in": pos["t"], "t_out": t, "hold_h": t - pos["t"],
                       "dir": "short_ast" if pos["short_ast"] else "short_hl",
                       "gross_entry": pos["gross"], "funding": pos["fund"](t), "basis": pos["basis"](t),
                       "cost": cost_usd, "pnl": pnl, "reason": reason})
        cool[(s, pos["short_ast"])] = t
        closes.append((t, pnl))

    for t in range(t_start, t_end):
        # ---- exits
        for s in list(open_):
            pos = open_[s]
            ph, pa = D.pxh[t, s], D.pxa[t, s]
            if np.isnan(ph) or np.isnan(pa):
                continue
            sp = ext[t, s]
            g = (sp if pos["short_ast"] else -sp) if not np.isnan(sp) else np.nan
            unreal = pos["basis"](t) + pos["fund"](t) - cost_usd
            div = abs(ph - pa) / ((ph + pa) / 2) * 100
            held = t - pos["t"]
            reason = None
            if unreal <= -p.stop_loss:
                reason = "stop_loss"
            elif div > p.max_px_div_exit:
                reason = "px_divergence"
            elif held >= p.max_hold_days * 24:
                reason = "max_hold"
            elif not np.isnan(g) and g < p.exit_apr and held >= p.min_hold_h:
                pos["below"] += 1
                if pos["below"] >= p.exit_confirm:
                    reason = "spread_collapsed"
            else:
                pos["below"] = 0
            if reason:
                close(s, t, reason)
        # ---- circuit breaker
        if t >= halt_until and sum(x for tt, x in closes if tt > t - 24) <= -p.daily_loss_limit:
            halt_until = t + p.halt_h
        # ---- entry (one per tick, best spread first)
        if t < halt_until or len(open_) >= p.max_pairs:
            continue
        row = ent[t]
        g = np.abs(row)
        ok = ~np.isnan(g) & (g >= p.min_entry_apr) & (g <= p.max_entry_apr)
        ok &= RT_COST_PCT / (np.where(g > 0, g, np.nan) / 365) <= p.max_breakeven_days
        ph, pa = D.pxh[t], D.pxa[t]
        ok &= ~np.isnan(ph) & ~np.isnan(pa)
        with np.errstate(invalid="ignore"):
            ok &= np.abs(ph - pa) / ((ph + pa) / 2) * 100 <= p.max_px_div_entry
        for s in np.argsort(-np.nan_to_num(g)):
            if not ok[s]:
                continue
            short_ast = bool(row[s] > 0)
            if s in open_ or t - cool.get((s, short_ast), -1e9) < p.cooldown_h:
                continue
            n = p.notional
            qh, qa = n / ph[s], n / pa[s]
            ph0, pa0, t0 = ph[s], pa[s], t

            def basis(tt, s=s, short_ast=short_ast, qh=qh, qa=qa, ph0=ph0, pa0=pa0):
                dh, da = D.pxh[tt, s] - ph0, D.pxa[tt, s] - pa0
                if np.isnan(dh) or np.isnan(da):
                    return 0.0
                # short_ast: long HL / short Aster; otherwise the reverse
                return qh * (dh if short_ast else -dh) + qa * (-da if short_ast else da)

            def fund(tt, s=s, short_ast=short_ast, t0=t0, n=p.notional):
                fh = D.cfh[tt, s] - D.cfh[t0, s]
                fa = D.cfa[tt, s] - D.cfa[t0, s]
                # short receives positive funding, long pays it
                return n * ((fa - fh) if short_ast else (fh - fa))

            open_[s] = {"t": t, "short_ast": short_ast, "gross": float(g[s]), "below": 0,
                        "basis": basis, "fund": fund}
            break
    for s in list(open_):
        last = t_end - 1
        while np.isnan(D.pxh[last, s]) or np.isnan(D.pxa[last, s]):
            last -= 1
        close(s, last, "end_of_test")
    return trades


def summarize(trades, p, days):
    if not trades:
        return {"trades": 0, "pnl": 0.0, "roi_pct": 0.0, "apr_pct": 0.0}
    t = pd.DataFrame(trades)
    capital = p.max_pairs * 2 * p.margin
    pnl = t["pnl"].sum()
    daily = t.assign(d=t["t_out"] // 24).groupby("d")["pnl"].sum()
    daily = daily.reindex(range(int(daily.index.min()), int(daily.index.max()) + 1), fill_value=0)
    eq = daily.cumsum()
    dd = (eq - eq.cummax()).min()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0.0
    return {"trades": len(t), "win_pct": round((t["pnl"] > 0).mean() * 100, 1), "pnl": round(pnl, 2),
            "funding": round(t["funding"].sum(), 2), "basis": round(t["basis"].sum(), 2),
            "costs": round(-t["cost"].sum(), 2), "avg_hold_h": round(t["hold_h"].mean(), 1),
            "roi_pct": round(pnl / capital * 100, 2), "apr_pct": round(pnl / capital * 365 / days * 100, 1),
            "max_dd": round(dd, 2), "sharpe": round(sharpe, 2)}
