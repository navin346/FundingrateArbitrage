"""Vercel serverless endpoint: GET /api/scan

Runs the Setu scanner once and returns every cross-venue spread worth showing
(gross APR >= 20%, liquidity >= $100k, price-match <= 5%) as JSON. The dashboard
(index.html) filters/sorts client-side, so one cached scan serves every viewer.
The CDN caches the response for 5 min and serves stale while revalidating."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app  # noqa: E402

COLS = ["sym", "long@", "short@", "gross_apr_%", "rt_cost_%", "pair_liq_$", "px_diverge_%"]


def run_scan():
    df, errors = app.fetch_all()
    pairs = app.build_pairs(df, app.DEFAULT_FEES, app.DEFAULT_SLIP, 7, 300, 3)
    total = len(pairs)
    if total:
        keep = pairs[(pairs["gross_apr_%"] >= 20) & (pairs["px_diverge_%"] <= 5)
                     & (pairs["pair_liq_$"].fillna(0) >= 100_000)]
        keep = keep.sort_values("gross_apr_%", ascending=False).head(600)
        rows = json.loads(keep[COLS].to_json(orient="records"))  # NaN -> null
    else:
        rows = []
    units = {v: g["unit_mode"].iloc[0] for v, g in df.groupby("venue")}
    return {
        "updated": __import__("time").time(),
        "venues": {v: int(n) for v, n in df.groupby("venue").size().items()},
        "markets": int(df["sym"].nunique()),
        "pair_combos": int(total),
        "unit_modes": units,
        "errors": errors,
        "pairs": rows,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body, status = json.dumps(run_scan()).encode(), 200
        except Exception as e:  # never leak a stack trace to the browser
            body, status = json.dumps({"error": str(e)[:300]}).encode(), 502
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if status == 200:
            self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=900")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
