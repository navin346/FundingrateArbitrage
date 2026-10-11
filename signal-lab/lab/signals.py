"""Each strategy turns the latest data into a side per asset: +1 long, -1 short, 0 flat.

Strategies run side by side, each with its own virtual $500, so the dashboard can show which
ideas actually win. COIN_FLIP and BUY_HOLD are controls: a signal is only interesting if it
beats them over many trades."""
import random

from lab import config as C
from lab.news import effect

DESCRIPTIONS = {
    "momentum_3d": "Trend: go with the last 3 days' direction",
    "momentum_7d": "Trend: go with the last 7 days' direction",
    "crowd_follow": "Positioning: go WITH the crowded side (funding far above baseline = longs paying = go long)",
    "crowd_fade": "Positioning: go AGAINST the crowded side (squeeze bet)",
    "news": "News: themes in the last 24h headlines, using measured reactions once learned",
    "combo": "Self-learning: weighted vote of the signals above, weights = their live win rate edge",
    "coin_flip": "CONTROL: random side every day",
    "buy_hold": "CONTROL: always long",
}
COMPONENTS = ["momentum_3d", "momentum_7d", "crowd_follow", "crowd_fade", "news"]


def _sign(x, dead=0.0):
    return 1 if x > dead else (-1 if x < -dead else 0)


def features(asset, candles, funding_hist, news_scores):
    closes = [c["c"] for c in candles]
    f = {"price": closes[-1] if closes else None}
    for d in (1, 3, 7):
        k = 24 * d
        f[f"ret_{d}d"] = (closes[-1] / closes[-1 - k] - 1) * 100 if len(closes) > k else None
    last24 = [r for _, r in funding_hist[-24:]]
    f["funding_apr_24h"] = sum(last24) / len(last24) * 24 * 365 * 100 if last24 else None
    f["news_score"] = news_scores.get(asset, 0.0)
    return f


def news_scores(recent_events, matrix):
    """Net news score per asset from theme events in the lookback window."""
    from collections import Counter
    counts = Counter(ev["theme"] for ev in recent_events)   # many headlines on one story != many signals
    out = {a: 0.0 for a in C.ASSETS}
    for theme, n in counts.items():
        w = min(n, 3)
        for a in C.ASSETS:
            out[a] += w * effect(theme, a, matrix)
    return out


def decide(feat, weights, day_key):
    """Return {strategy: {asset: side}}."""
    out = {s: {} for s in DESCRIPTIONS}
    rnd = random.Random(day_key)                     # reproducible coin flips per day
    for a, f in feat.items():
        r3, r7, fr = f.get("ret_3d"), f.get("ret_7d"), f.get("funding_apr_24h")
        out["momentum_3d"][a] = _sign(r3, C.MOMENTUM_MIN_MOVE_PCT) if r3 is not None else 0
        out["momentum_7d"][a] = _sign(r7, C.MOMENTUM_MIN_MOVE_PCT) if r7 is not None else 0
        crowd = _sign(fr - C.HL_BASELINE_FUNDING_APR, C.CROWDING_APR) if fr is not None else 0
        out["crowd_follow"][a] = crowd
        out["crowd_fade"][a] = -crowd
        out["news"][a] = _sign(f["news_score"], C.NEWS_MIN_SCORE - 1e-9)
        vote = sum(weights.get(s, 0.0) * out[s][a] for s in COMPONENTS)
        out["combo"][a] = _sign(vote, 1e-9)
        out["coin_flip"][a] = rnd.choice([-1, 1])
        out["buy_hold"][a] = 1
    return out


def learn_weights(closed_trades):
    """Weight for each component = how far its recent win rate is above 50% (never negative).
    Until a component has enough trades, it gets weight 1 (equal vote)."""
    w = {}
    for s in COMPONENTS:
        tr = [t for t in closed_trades if t["strategy"] == s][-C.COMBO_WINDOW:]
        if len(tr) < C.COMBO_WARMUP_TRADES:
            w[s] = 1.0
        else:
            wr = sum(t["pnl"] > 0 for t in tr) / len(tr)
            w[s] = round(max(0.0, wr - 0.5) * 10, 3)
    return w
