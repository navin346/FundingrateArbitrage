"""Paper broker: one virtual account per strategy.

Fills at the mark price plus slippage, charges Hyperliquid taker fees, accrues funding every
hour, and checks stop-loss / take-profit against each hourly candle's high and low (if a candle
touches both, the stop is assumed to hit first: conservative)."""
from lab import config as C


def new_account():
    return {"cash": C.START_EQUITY, "positions": {}}


def _cost(asset, notional):
    return notional * (C.ASSETS[asset]["fee_bps"] + C.SLIPPAGE_BPS) / 1e4


def equity(acct, marks):
    eq = acct["cash"]
    for a, p in acct["positions"].items():
        px = marks.get(a, p["entry"])
        eq += p["side"] * p["qty"] * (px - p["entry"])
    return eq


def close(acct, asset, px, ts, reason, log, strategy):
    p = acct["positions"].pop(asset)
    gross = p["side"] * p["qty"] * (px - p["entry"])
    fee = _cost(asset, p["qty"] * px)
    acct["cash"] += gross - fee
    pnl = gross - fee - p["fee_in"] + p["funding"]
    log.append({"strategy": strategy, "asset": asset, "side": "long" if p["side"] > 0 else "short",
                "opened": p["opened"], "closed": ts, "entry": p["entry"], "exit": px,
                "move_pct": round(p["side"] * (px / p["entry"] - 1) * 100, 3),
                "pnl": round(pnl, 4), "funding": round(p["funding"], 4), "reason": reason})


def open_(acct, asset, side, px, ts, notional):
    qty = notional / px
    fee = _cost(asset, notional)
    acct["cash"] -= fee
    stop = px * (1 - side * C.STOP_PCT / 100)
    tgt = px * (1 + side * C.TARGET_PCT / 100)
    acct["positions"][asset] = {"side": side, "qty": qty, "entry": px, "stop": stop, "target": tgt,
                                "opened": ts, "fee_in": fee, "funding": 0.0}


def check_stops(acct, asset, bars, log, strategy):
    """bars: hourly candles since the last check, oldest first."""
    p = acct["positions"].get(asset)
    if not p:
        return
    for b in bars:
        if b["t"] < p["opened"] - 3_600_000:
            continue
        hit_stop = b["l"] <= p["stop"] if p["side"] > 0 else b["h"] >= p["stop"]
        hit_tgt = b["h"] >= p["target"] if p["side"] > 0 else b["l"] <= p["target"]
        if hit_stop:
            close(acct, asset, p["stop"], b["t"] + 3_600_000, "stop-loss", log, strategy)
            return
        if hit_tgt:
            close(acct, asset, p["target"], b["t"] + 3_600_000, "take-profit", log, strategy)
            return


def accrue_funding(acct, asset, hourly_rate, hours, mark):
    p = acct["positions"].get(asset)
    if p and hours > 0:
        pay = -p["side"] * p["qty"] * mark * hourly_rate * hours     # longs pay positive funding
        p["funding"] += pay
        acct["cash"] += pay


def rebalance(acct, targets, marks, ts, log, strategy):
    """Move the account to the target sides. Keep positions whose side is unchanged."""
    eq = equity(acct, marks)
    per_asset = max(0.0, eq) * C.LEVERAGE / len(C.ASSETS)
    for a, side in targets.items():
        px = marks.get(a)
        if px is None:
            continue
        cur = acct["positions"].get(a)
        if cur and cur["side"] == side:
            continue
        if cur:
            close(acct, a, px * (1 - cur["side"] * C.SLIPPAGE_BPS / 1e4), ts, "signal change", log, strategy)
        if side != 0 and per_asset > 1:
            open_(acct, a, side, px * (1 + side * C.SLIPPAGE_BPS / 1e4), ts, per_asset)
