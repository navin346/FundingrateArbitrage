"""Reaction matrix: how did each asset ACTUALLY move after each news theme?

Every theme hit is logged with the asset prices at the moment the bot saw it. On later runs
the +4h and +24h returns are filled in. The matrix (theme x asset) keeps running mean / sd /
count of the 24h reaction, which news.effect() uses instead of the prior once n is large."""
import math

HOUR = 3_600_000


def log_events(state, themed, marks, ts):
    for it in themed:
        for th in it["themes"]:
            state["events"].append({"id": it["id"], "theme": th, "seen": ts, "title": it["title"][:160],
                                    "px": {a: marks.get(a) for a in marks}, "ret_4h": None, "ret_24h": None})
    state["events"] = state["events"][-3000:]


def _price_at(bars_by_asset, a, t):
    """Close of the first hourly bar ending at/after t."""
    for b in bars_by_asset.get(a, []):
        if b["t"] + HOUR >= t:
            return b["c"]
    return None


def fill_returns(state, bars_by_asset, now):
    m = state["matrix"]
    for ev in state["events"]:
        for key, h in (("ret_4h", 4), ("ret_24h", 24)):
            if ev[key] is not None or now - ev["seen"] < h * HOUR:
                continue
            out = {}
            for a, p0 in ev["px"].items():
                p1 = _price_at(bars_by_asset, a, ev["seen"] + h * HOUR)
                if p0 and p1:
                    out[a] = round((p1 / p0 - 1) * 100, 4)
            ev[key] = out
            if key == "ret_24h":
                for a, r in out.items():
                    cell = m.setdefault(ev["theme"], {}).setdefault(a, {"n": 0, "sum": 0.0, "sumsq": 0.0, "hits": 0})
                    cell["n"] += 1
                    cell["sum"] += r
                    cell["sumsq"] += r * r
                    from lab.news import THEMES
                    prior = THEMES.get(ev["theme"], {}).get("prior", {}).get(a, 0)
                    cell["hits"] += int(prior != 0 and r * prior > 0)
                    n = cell["n"]
                    mean = cell["sum"] / n
                    cell["mean_24h"] = round(mean, 4)
                    cell["sd_24h"] = round(math.sqrt(max(0.0, cell["sumsq"] / n - mean * mean)), 4)
