"""Setu: funding-rate arbitrage radar across perp DEXes.
Venues: Lighter, Aster, Paradex, Variational, Hyperliquid (reference).
All public read-only APIs, no keys. Run headless: python app.py
Deploy: Streamlit Community Cloud, main file app.py
"""
import math, time, json
import requests
import pandas as pd

H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
HOURS_YEAR = 8760.0

# editable venue fee defaults (bps per side, taker). Lighter fees read live.
DEFAULT_FEES = {"hyperliquid": 4.5, "lighter": 0.0, "aster": 4.0, "paradex": 3.0, "variational": 0.0}
DEFAULT_SLIP = {"hyperliquid": 3.0, "lighter": 6.0, "aster": 6.0, "paradex": 6.0, "variational": None}  # None: live quoted spread


def _get(url, params=None, timeout=15):
    r = requests.get(url, params=params, timeout=timeout, headers=H)
    r.raise_for_status()
    return r.json()


def _post(url, payload, timeout=15):
    r = requests.post(url, json=payload, timeout=timeout, headers=H)
    r.raise_for_status()
    return r.json()


# same asset, different names across venues -> one canonical symbol
ALIASES = {
    "XAU": "GOLD", "XAUT": "GOLD", "PAXG": "GOLD", "XAG": "SILVER", "HG": "COPPER",
    "CL": "OIL", "WTI": "OIL", "USOIL": "OIL", "CRUDE": "OIL",
    "BRENTOIL": "BRENT", "BRN": "BRENT", "UKOIL": "BRENT",
    "NATGAS": "NG", "XNG": "NG",
    "XYZ100": "NDX", "NAS100": "NDX", "NASDAQ100": "NDX", "US100": "NDX",
    "SP500": "SPX", "SPX500": "SPX", "US500": "SPX",
    "RNDR": "RENDER", "MATIC": "POL",
}
# k/1000-denominated listings (explicit list: a blanket K rule mangles KAVA, KAITO...)
KDENOM = {"KPEPE": ("PEPE", 1e3), "KBONK": ("BONK", 1e3), "KSHIB": ("SHIB", 1e3),
          "KFLOKI": ("FLOKI", 1e3), "KLUNC": ("LUNC", 1e3), "KNEIRO": ("NEIRO", 1e3),
          "KDOGS": ("DOGS", 1e3)}


def norm_sym(s):
    s = s.upper()
    if s.startswith("XYZ:"):
        s = s[4:]
    s = s.replace("-USD-PERP", "").replace("-PERP", "")
    for suf in ("USDT", "USDC", "USD"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    mult = 1.0
    if s in KDENOM:
        s, mult = KDENOM[s]
    else:
        for pre, m in (("1000000", 1e6), ("1M", 1e6), ("1000", 1e3)):
            if s.startswith(pre) and len(s) > len(pre):
                s, mult = s[len(pre):], m
                break
    return ALIASES.get(s, s), mult


def fetch_hyperliquid():
    """Core crypto dex + the trade.xyz HIP-3 dex (gold, oil, indices, stocks).
    xyz markets carry 2x fees (9 bps), set per row."""
    rows = []
    for dex, fee in ((None, 4.5), ("xyz", 9.0)):
        payload = {"type": "metaAndAssetCtxs"}
        if dex:
            payload["dex"] = dex
        try:
            meta, ctxs = _post("https://api.hyperliquid.xyz/info", payload)
        except Exception:
            continue
        for u, c in zip(meta["universe"], ctxs):
            try:
                mp = float(c["markPx"])
                if mp <= 0:
                    continue
                sym, mult = norm_sym(u["name"])
                rows.append({"venue": "hyperliquid", "sym": sym, "raw": u["name"], "mult": mult,
                             "mark": mp / mult,
                             "rate": float(c["funding"]), "interval_h": 1.0,
                             "oi_usd": float(c["openInterest"]) * mp,
                             "vol24": float(c.get("dayNtlVlm") or 0),
                             "spread_bps": None, "fee_bps": fee})
            except Exception:
                continue
    df = pd.DataFrame(rows)
    return df.sort_values("vol24", ascending=False).drop_duplicates(["sym"])


def fetch_lighter():
    fr = _get("https://mainnet.zklighter.elliot.ai/api/v1/funding-rates")
    rates = {x["market_id"]: float(x["rate"]) for x in fr.get("funding_rates", [])
             if x.get("exchange") == "lighter"}
    obd = _get("https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails")
    stats = {}
    try:
        es = _get("https://mainnet.zklighter.elliot.ai/api/v1/exchangeStats")
        stats = {x["symbol"]: x for x in es.get("order_book_stats", [])}
    except Exception:
        pass
    rows = []
    for ob in obd.get("order_book_details", []):
        mid = ob.get("market_id")
        if mid not in rates or ob.get("status") != "active":
            continue
        try:
            sym, mult = norm_sym(ob["symbol"])
            st_ = stats.get(ob["symbol"], {})
            mark = float(st_.get("last_trade_price") or ob.get("last_trade_price") or 0) / mult
            oi = ob.get("open_interest")
            oi_usd = float(oi) * mark * mult if oi not in (None, "") else None
            rows.append({"venue": "lighter", "sym": sym, "raw": ob["symbol"], "mult": mult,
                         "mark": mark,
                         "rate": rates[mid], "interval_h": 1.0,
                         "oi_usd": oi_usd,
                         "vol24": float(st_.get("daily_quote_token_volume") or 0),
                         "spread_bps": None,
                         "fee_bps": float(ob.get("taker_fee") or 0) * 100})
        except Exception:
            continue
    return pd.DataFrame(rows)


def fetch_aster():
    prem = _get("https://fapi.asterdex.com/fapi/v1/premiumIndex")
    tick = {t["symbol"]: t for t in _get("https://fapi.asterdex.com/fapi/v1/ticker/24hr")}
    intervals = {}
    try:
        intervals = {f["symbol"]: float(f.get("fundingIntervalHours") or 8)
                     for f in _get("https://fapi.asterdex.com/fapi/v1/fundingInfo")}
    except Exception:
        pass
    rows = []
    for p in prem:
        s = p["symbol"]
        if not s.endswith(("USDT", "USD", "USDC")):
            continue
        try:
            sym, mult = norm_sym(s)
            mark = float(p["markPrice"]) / mult
            rows.append({"venue": "aster", "sym": sym, "raw": s, "mult": mult, "mark": mark,
                         "rate": float(p["lastFundingRate"]),
                         "interval_h": intervals.get(s, 8.0),
                         "oi_usd": None,
                         "vol24": float(tick.get(s, {}).get("quoteVolume") or 0),
                         "spread_bps": None})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    return df.sort_values("vol24", ascending=False).drop_duplicates("sym")


def fetch_paradex():
    meta = _get("https://api.prod.paradex.trade/v1/markets")["results"]
    periods = {m["symbol"]: float(m.get("funding_period_hours") or 8)
               for m in meta if m.get("asset_kind") == "PERP"}
    summ = _get("https://api.prod.paradex.trade/v1/markets/summary", {"market": "ALL"})["results"]
    rows = []
    for s in summ:
        if s.get("symbol") not in periods:
            continue
        try:
            sym, mult = norm_sym(s["symbol"])
            mark = float(s["mark_price"]) / mult
            bid, ask = float(s.get("bid") or 0), float(s.get("ask") or 0)
            spread = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > 0 else None
            oi = float(s.get("open_interest") or 0) * float(s["mark_price"])
            rows.append({"venue": "paradex", "sym": sym, "raw": s["symbol"], "mult": mult,
                         "mark": mark,
                         "rate": float(s.get("funding_rate") or 0),
                         "interval_h": periods[s["symbol"]],
                         "oi_usd": oi, "vol24": float(s.get("volume_24h") or 0),
                         "spread_bps": spread})
        except Exception:
            continue
    return pd.DataFrame(rows)


def fetch_variational():
    j = _get("https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats")
    rows = []
    for l in j.get("listings", []):
        try:
            sym, mult = norm_sym(l["ticker"])
            mark = float(l["mark_price"]) / mult
            oi = l.get("open_interest") or {}
            oi_usd = float(oi.get("long_open_interest") or 0) + float(oi.get("short_open_interest") or 0)
            q = (l.get("quotes") or {}).get("size_1k") or {}
            bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
            spread = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > 0 else \
                     (float(l["base_spread_bps"]) if l.get("base_spread_bps") else None)
            rows.append({"venue": "variational", "sym": sym, "raw": l["ticker"], "mult": mult,
                         "mark": mark,
                         "rate": float(l.get("funding_rate") or 0),
                         "interval_h": float(l.get("funding_interval_s") or 28800) / 3600.0,
                         "oi_usd": oi_usd, "vol24": float(l.get("volume_24h") or 0),
                         "spread_bps": spread})
        except Exception:
            continue
    return pd.DataFrame(rows)


def calibrate_apr(df, ref):
    """Venue docs disagree on funding units (fraction vs percent, per-interval vs other).
    Pick, per venue, the unit interpretation whose APR best matches Hyperliquid's on
    shared majors. HL is ground truth: hourly fraction."""
    out = []
    candidates = {"frac_per_interval": lambda r, h: r * (HOURS_YEAR / h) * 100,
                  "pct_per_interval": lambda r, h: r * (HOURS_YEAR / h),
                  "frac_per_8h": lambda r, h: r * (HOURS_YEAR / 8) * 100,
                  "pct_per_8h": lambda r, h: r * (HOURS_YEAR / 8),
                  "frac_annual": lambda r, h: r * 100,
                  "pct_annual": lambda r, h: r}
    refm = ref.set_index("sym")["apr"]
    for venue, g in df.groupby("venue"):
        best, best_err, best_mode = None, 1e18, None
        for mode, f in candidates.items():
            apr = g.apply(lambda r: f(r["rate"], r["interval_h"]), axis=1)
            shared = g.assign(apr=apr).set_index("sym").join(refm.rename("ref"), how="inner")
            shared = shared[(shared["ref"].abs() > 3) & (shared["apr"].abs() > 0)]
            if len(shared) < 3:
                err = 0 if mode == "frac_per_interval" else 1e17  # default to fraction
            else:
                err = (shared["apr"].abs().apply(math.log1p) - shared["ref"].abs().apply(math.log1p)).abs().median()
            if err < best_err:
                best_err, best, best_mode = err, g.assign(apr=apr), mode
        best["unit_mode"] = best_mode
        out.append(best)
    return pd.concat(out) if out else df.assign(apr=0.0)


def fetch_all():
    fetchers = {"hyperliquid": fetch_hyperliquid, "lighter": fetch_lighter,
                "aster": fetch_aster, "paradex": fetch_paradex, "variational": fetch_variational}
    frames, errors = [], {}
    for name, fn in fetchers.items():
        try:
            d = fn()
            if len(d):
                frames.append(d)
        except Exception as e:
            errors[name] = str(e)[:120]
    df = pd.concat(frames, ignore_index=True)
    hl = df[df.venue == "hyperliquid"].copy()
    hl["apr"] = hl["rate"] * HOURS_YEAR * 100
    rest = calibrate_apr(df[df.venue != "hyperliquid"], hl)
    hl["unit_mode"] = "frac_per_interval"
    alldf = pd.concat([hl, rest], ignore_index=True)
    alldf["liq_usd"] = alldf[["oi_usd", "vol24"]].min(axis=1, skipna=True)
    return alldf, errors


def build_pairs(df, fees_bps, slip_bps, hold_days, margin, lev):
    rows = []
    venues = sorted(df.venue.unique())
    notional = margin * lev
    for i in range(len(venues)):
        for j in range(i + 1, len(venues)):
            a, b = venues[i], venues[j]
            da = df[df.venue == a].set_index("sym")
            db = df[df.venue == b].set_index("sym")
            for sym in da.index.intersection(db.index):
                ra, rb = da.loc[sym], db.loc[sym]
                if isinstance(ra, pd.DataFrame) or isinstance(rb, pd.DataFrame):
                    continue
                if not (ra["mark"] > 0 and rb["mark"] > 0):
                    continue
                div = abs(ra["mark"] - rb["mark"]) / ((ra["mark"] + rb["mark"]) / 2) * 100
                gross = ra["apr"] - rb["apr"]
                short_v, long_v = (a, b) if gross > 0 else (b, a)
                gross = abs(gross)
                cost_pct = 0.0
                for leg, v in ((ra, a), (rb, b)):
                    fee = leg.get("fee_bps") if pd.notna(leg.get("fee_bps", float("nan"))) else fees_bps.get(v, 3.0)
                    slip = leg["spread_bps"] if pd.notna(leg["spread_bps"]) else slip_bps.get(v) or 8.0
                    cost_pct += (2 * fee + slip) / 100.0  # roundtrip per leg, in %
                be_days = cost_pct / (gross / 365) if gross > 0 else float("inf")
                net_hold = gross - cost_pct * (365 / hold_days)
                liq = pd.Series([ra["liq_usd"], rb["liq_usd"]]).min(skipna=True)
                rows.append({"sym": sym, "long@": long_v, "short@": short_v,
                             "apr_" + a: round(ra["apr"], 1), "apr_" + b: round(rb["apr"], 1),
                             "gross_apr_%": round(gross, 1), "rt_cost_%": round(cost_pct, 3),
                             "breakeven_days": round(be_days, 1),
                             f"net_apr_{hold_days}d_%": round(net_hold, 1),
                             "pair_liq_$": int(liq) if pd.notna(liq) else None,
                             "$/day_at_size": round(notional * gross / 36500, 2),
                             "px_diverge_%": round(div, 2), "pair": f"{a}~{b}"})
    p = pd.DataFrame(rows)
    if len(p):
        p = p.sort_values("gross_apr_%", ascending=False)
    return p


def qualified(pairs, min_liq, min_apr, hold_days, max_div=2.0):
    if not len(pairs):
        return pairs
    q = pairs[(pairs["gross_apr_%"] >= min_apr) & (pairs["px_diverge_%"] <= max_div)]
    q = q[(q["pair_liq_$"].fillna(0) >= min_liq)]
    return q


# ---------------- Streamlit UI ----------------
def main():
    import streamlit as st
    st.set_page_config(page_title="Setu: funding arb radar", page_icon="🌉", layout="wide")
    st.title("Setu 🌉 cross-DEX funding arbitrage radar")
    st.caption("Lighter · Aster · Paradex · Variational · Hyperliquid — all pairwise combinations, "
               "interval-normalized funding, net of fees and spread. Public read-only APIs, no keys.")

    sb = st.sidebar
    sb.header("Filters & sizing")
    min_apr = sb.number_input("Min gross APR %", 0.0, 1000.0, 120.0, 10.0)
    min_liq = sb.number_input("Min pair liquidity $", 0, 50_000_000, 500_000, 50_000)
    hold = sb.number_input("Planned hold (days)", 1, 90, 7)
    margin = sb.number_input("Margin per leg $", 50, 100000, 300, 50)
    lev = sb.slider("Leverage per leg", 1, 10, 3)
    sb.caption(f"Notional/leg: ${margin*lev:,} · approx liq-distance at {lev}x: ~{100/lev - 1:.0f}% price move kills a leg")
    venues_on = sb.multiselect("Venues", ["hyperliquid", "lighter", "aster", "paradex", "variational"],
                               default=["lighter", "aster", "paradex", "variational", "hyperliquid"])

    @st.cache_data(ttl=1800, show_spinner="Scanning 5 venues...")
    def load():
        return fetch_all()

    if sb.button("Force refresh now"):
        load.clear()
    df, errors = load()
    df = df[df.venue.isin(venues_on)]
    pairs = build_pairs(df, DEFAULT_FEES, DEFAULT_SLIP, hold, margin, lev)
    q = qualified(pairs, min_liq, min_apr, hold)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Venues live", df.venue.nunique())
    c2.metric("Markets scanned", df.sym.nunique())
    c3.metric("Pair combos", len(pairs))
    c4.metric("Qualified now", len(q))
    if errors:
        st.warning("Venue errors: " + json.dumps(errors))
    st.caption(f"Snapshot: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · data cached 30 min · "
               "breakeven_days = days of funding needed to pay entry+exit costs")

    st.subheader(f"Qualified: ≥{min_apr:.0f}% gross APR, ≥${min_liq:,} liquidity, price match ≤2%")
    if len(q):
        st.dataframe(q.drop(columns=[c for c in q.columns if c.startswith("apr_")]),
                     use_container_width=True, height=400)
    else:
        st.info("Nothing passes the bar right now. Funding spreads breathe — lower the APR filter or wait.")

    with st.expander(f"All {len(pairs)} cross-venue spreads (unfiltered)"):
        st.dataframe(pairs, use_container_width=True, height=500)
    with st.expander("Raw per-venue funding table"):
        st.dataframe(df[["venue", "sym", "mark", "apr", "interval_h", "oi_usd", "vol24", "spread_bps", "unit_mode"]]
                     .sort_values("apr", key=abs, ascending=False), use_container_width=True, height=500)

    st.caption("Worst case per pair ≈ one leg's margin (liquidation breaks neutrality). Funding flips; "
               "high APRs on illiquid names are squeeze-risk premium, not free money. Not investment advice.")


def _smoke_test():
        df, errors = fetch_all()
        print("errors:", errors)
        print(df.groupby("venue").agg(n=("sym", "count"), mode=("unit_mode", "first")))
        btc = df[df.sym == "BTC"][["venue", "apr", "interval_h", "mark"]]
        print("BTC APR by venue:\n", btc.to_string(index=False))
        pairs = build_pairs(df, DEFAULT_FEES, DEFAULT_SLIP, 7, 300, 3)
        q = qualified(pairs, 500_000, 120, 7)
        print(f"\npairs={len(pairs)} qualified(>=120% & >=500k)={len(q)}")
        cols = ["sym", "long@", "short@", "gross_apr_%", "rt_cost_%", "breakeven_days", "net_apr_7d_%", "pair_liq_$", "px_diverge_%"]
        print("\nTOP QUALIFIED:\n", q[cols].head(12).to_string(index=False) if len(q) else "none")
        loose = qualified(pairs, 500_000, 40, 7)
        print("\nTOP at >=40% APR:\n", loose[cols].head(12).to_string(index=False) if len(loose) else "none")
        tradfi = pairs[pairs.sym.isin(["GOLD", "SILVER", "COPPER", "OIL", "BRENT", "NG",
                                       "NDX", "SPX", "NVDA", "TSLA", "MSFT", "AAPL", "GME"])]
        tq = tradfi[(tradfi["pair_liq_$"].fillna(0) >= 250_000) & (tradfi["px_diverge_%"] <= 2)]
        print("\nUNLOCKED TRADFI ARBS (>=250k liq):\n",
              tq[cols].head(15).to_string(index=False) if len(tq) else "none")


if __name__ == "__main__":
    try:
        import streamlit.runtime
        if streamlit.runtime.exists():
            main()
        else:
            raise RuntimeError
    except Exception:
        _smoke_test()
