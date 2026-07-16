"""Thin wrapper around the Setu radar (app.py) that returns market data in the
shape the engine needs: a (venue, sym) -> row map plus the ranked pairs table."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402


def scan(cfg):
    """Returns (rows, pairs, errors).
    rows: dict (venue, sym) -> dict with mark, apr, raw, mult, spread_bps, liq_usd
    pairs: DataFrame from app.build_pairs, sorted by gross APR desc
    errors: dict venue -> error string for venues that failed to fetch
    """
    df, errors = app.fetch_all()
    df = df.drop_duplicates(["venue", "sym"])
    pairs = app.build_pairs(df, app.DEFAULT_FEES, app.DEFAULT_SLIP,
                            hold_days=7, margin=cfg.margin_per_leg, lev=cfg.leverage)
    rows = {}
    for _, r in df.iterrows():
        rows[(r["venue"], r["sym"])] = r.to_dict()
    return rows, pairs, errors
