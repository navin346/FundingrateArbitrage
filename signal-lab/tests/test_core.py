"""Core accounting tests: python -m pytest -q  (or python tests/test_core.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab import config as C, learn, paper, signals  # noqa: E402

H = 3_600_000


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def test_long_stop_and_fees():
    acct, log = paper.new_account(), []
    paper.open_(acct, "BTC", 1, 100.0, 0, 750.0)
    paper.check_stops(acct, "BTC", [bar(0, 100, 101, 97.5, 98)], log, "t")
    assert not acct["positions"] and log[0]["reason"] == "stop-loss"
    fee = 750 * (C.ASSETS["BTC"]["fee_bps"] + C.SLIPPAGE_BPS) / 1e4
    exp = 7.5 * (98.0 - 100.0) - fee - 7.5 * 98.0 * (C.ASSETS["BTC"]["fee_bps"] + C.SLIPPAGE_BPS) / 1e4
    assert abs(log[0]["pnl"] - exp) < 1e-3, (log[0]["pnl"], exp)
    assert abs(acct["cash"] - (C.START_EQUITY + exp)) < 1e-6


def test_short_target_and_both_touched_means_stop():
    acct, log = paper.new_account(), []
    paper.open_(acct, "ETH", -1, 100.0, 0, 750.0)
    paper.check_stops(acct, "ETH", [bar(0, 100, 100.5, 97.9, 98)], log, "t")
    assert log[-1]["reason"] == "take-profit" and log[-1]["move_pct"] > 0
    paper.open_(acct, "ETH", -1, 100.0, 0, 750.0)
    paper.check_stops(acct, "ETH", [bar(0, 100, 102.5, 97.5, 100)], log, "t")
    assert log[-1]["reason"] == "stop-loss"


def test_funding_sign():
    acct = paper.new_account()
    paper.open_(acct, "BTC", 1, 100.0, 0, 1000.0)
    c0 = acct["cash"]
    paper.accrue_funding(acct, "BTC", 0.0001, 10, 100.0)   # positive funding: longs pay
    assert acct["cash"] < c0
    acct2 = paper.new_account()
    paper.open_(acct2, "BTC", -1, 100.0, 0, 1000.0)
    c1 = acct2["cash"]
    paper.accrue_funding(acct2, "BTC", 0.0001, 10, 100.0)
    assert acct2["cash"] > c1


def test_reaction_matrix_learns():
    state = {"events": [], "matrix": {}}
    for i in range(12):
        learn.log_events(state, [{"id": str(i), "title": "x", "themes": ["fed_hawkish"]}], {"GOLD": 100.0}, i * H)
    bars = {"GOLD": [bar(t * H, 0, 0, 0, 105.0) for t in range(0, 60)]}
    learn.fill_returns(state, bars, 100 * H)
    cell = state["matrix"]["fed_hawkish"]["GOLD"]
    assert cell["n"] == 12 and cell["mean_24h"] == 5.0
    from lab.news import effect
    assert effect("fed_hawkish", "GOLD", state["matrix"]) == 1    # prior said -1; data says +1 -> data wins


def test_combo_weights():
    tr = [{"strategy": "news", "pnl": 1.0}] * 30 + [{"strategy": "news", "pnl": -1.0}] * 10
    w = signals.learn_weights(tr)
    assert w["news"] > 0 and w["momentum_3d"] == 1.0
    tr = [{"strategy": "news", "pnl": -1.0}] * 40
    assert signals.learn_weights(tr)["news"] == 0.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
