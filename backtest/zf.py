"""Lighter x Paradex dataset in the same shape as sim.Data, so the same tested
simulators (sim.simulate, carry.run_carry) run on it unchanged.

Mapping onto sim.Data's two legs:
  "h" leg = Lighter,  "a" leg = Paradex
  fh[i] = Lighter funding paid by a LONG over hour (i-1, i], as a fraction of notional
          = sign(direction) * value / price      (value = USD per 1 unit, docs: -pos*index*rate)
  fa[i] = Paradex funding paid by a LONG over (i-1, i]
          = (funding_index[i] - funding_index[i-1]) / price   (docs: -pos * dIndex)
  hl_apr / ast_apr = what a bot SEES at hour i (Lighter's just-settled hourly rate,
          Paradex's published 8h rate), annualised in %. Signals never use future data.
  spread = ast_apr - hl_apr  (>0: short Paradex / long Lighter)
Per-symbol round-trip execution cost (both venues, open+close, % of notional) comes
from data/zf_spreads.json (measured order books); fees are 0 on both venues for this flow."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.sim import HOURS_YEAR, Data

HOUR = 3_600_000


class ZFData(Data):
    def __init__(self, folder="data/zf", spreads="data/zf_spreads.json", cost_view="paradex_retail",
                 default_cost=None, min_hours=24 * 30):
        raw = [json.loads(f.read_text()) for f in sorted(Path(folder).glob("*.json"))]
        raw = [r for r in raw if r["lighter_funding"] and r["paradex_index"] and r["lighter_px"] and r["paradex_px"]]
        lo = min(min(r["paradex_index"][0][0], r["lighter_px"][0][0]) for r in raw) // HOUR
        hi = max(r["paradex_index"][-1][0] for r in raw) // HOUR
        n = hi - lo + 1
        self.t0_ms = lo * HOUR
        keep, cols = [], {k: [] for k in ("pxh", "pxa", "fh", "fa", "hl_apr", "ast_apr")}
        self.checks = {}
        for r in raw:
            pxh, pxa, fh, fa, ha, aa = (np.full(n, np.nan) for _ in range(6))
            for t, c in r["lighter_px"]:
                i = t // HOUR - lo + 1
                if 0 <= i < n:
                    pxh[i] = c / r["li_mult"]
            for t, c in r["paradex_px"]:
                i = t // HOUR - lo + 1
                if 0 <= i < n:
                    pxa[i] = c / r["px_mult"]
            pxh_f = pd.Series(pxh).ffill(limit=3).values
            pxa_f = pd.Series(pxa).ffill(limit=3).values
            # Lighter: per-unit USD value / unit price (both in Lighter's own units) -> fraction of notional
            vr = []
            for t, val, rate, dirn in r["lighter_funding"]:
                i = t // HOUR - lo
                if 0 <= i < n:
                    sgn = 1.0 if dirn == "long" else -1.0
                    px_unit = pxh_f[i] * r["li_mult"] if not np.isnan(pxh_f[i]) else np.nan
                    frac = sgn * val / px_unit if px_unit and not np.isnan(px_unit) else sgn * rate / 100
                    fh[i] = frac
                    ha[i] = frac * HOURS_YEAR * 100
                    if not np.isnan(px_unit) and rate:
                        vr.append(val / px_unit / (rate / 100))
            # Paradex: hourly index samples -> exact accrued funding per unit -> fraction of notional
            idx = np.full(n, np.nan)
            for t, ix, r8 in r["paradex_index"]:
                i = t // HOUR - lo
                if 0 <= i < n:
                    idx[i] = ix
                    aa[i] = r8 * 3 * 365 * 100           # published 8h rate, annualised %
            d = np.diff(idx, prepend=np.nan)
            unit_px = pxa_f * r["px_mult"]
            fa = np.where(np.isnan(d), 0.0, d / unit_px)
            fa = np.nan_to_num(fa)
            fh = np.nan_to_num(fh)
            if np.isfinite(pxh).sum() < min_hours or np.isfinite(pxa).sum() < min_hours:
                continue
            # sanity: realised Paradex funding should match its published rate on average
            pub = np.nanmean(aa) if np.isfinite(aa).any() else np.nan
            realised = fa[np.isfinite(idx)].sum() / max(1, np.isfinite(idx).sum()) * HOURS_YEAR * 100
            self.checks[r["sym"]] = {"lighter_value_over_rate": float(np.median(vr)) if vr else np.nan,
                                     "paradex_published_apr": float(pub), "paradex_realised_apr": float(realised)}
            keep.append(r)
            for k, v in (("pxh", pxh), ("pxa", pxa), ("fh", fh), ("fa", fa), ("hl_apr", ha), ("ast_apr", aa)):
                cols[k].append(v)
        self.syms = [r["sym"] for r in keep]
        self.k, self.n = len(keep), n
        st = lambda k: np.column_stack(cols[k])
        self.pxh = pd.DataFrame(st("pxh")).ffill(limit=3).values
        self.pxa = pd.DataFrame(st("pxa")).ffill(limit=3).values
        self.fh, self.fa = st("fh"), st("fa")
        self.hl_apr = pd.DataFrame(st("hl_apr")).ffill(limit=3).values
        self.ast_apr = pd.DataFrame(st("ast_apr")).ffill(limit=3).values
        self.spread = self.ast_apr - self.hl_apr
        self.cfh, self.cfa = np.cumsum(self.fh, 0), np.cumsum(self.fa, 0)
        self._roll = {}
        # volume proxy for universe tests: Paradex 24h volume recorded at download time if present
        self.vol = [r.get("vol", 0.0) for r in keep]
        # measured per-symbol round-trip execution cost (Lighter + Paradex), % of notional
        self.rt_cost = None
        self.cost_info = {}
        sp = Path(spreads)
        if sp.exists():
            med = json.loads(sp.read_text())["median_rt_cost_pct"]
            costs = []
            for s in self.syms:
                m = med.get(s, {})
                li, px = m.get("lighter"), m.get(cost_view)
                c = (li + px) if li is not None and px is not None else None
                self.cost_info[s] = {"lighter": li, "paradex": px, "total": c}
                costs.append(c)
            known = [c for c in costs if c is not None]
            fill = default_cost if default_cost is not None else (float(np.percentile(known, 90)) if known else 0.2)
            self.rt_cost = np.array([c if c is not None else fill for c in costs])
            self.cost_fill = fill
