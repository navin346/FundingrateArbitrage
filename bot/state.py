"""Crash-safe JSON persistence for bot state (open pairs, realized PnL,
halts, cooldowns). Atomic write via tmp+rename so a kill mid-save can't
corrupt the file."""
import json
import os
import time

DEFAULT_STATE = {
    "pairs": {},        # key -> open pair record
    "realized": [],     # [{ts, key, usd, reason}] trailing history
    "cooldown": {},     # key -> ts of last exit
    "halt_until": 0,    # no new entries before this unix ts
    "halt_reason": "",
    "err_streak": 0,
    "last_tick": 0,
    "last_heartbeat": 0,
}


def load(path):
    try:
        with open(path) as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    merged = dict(DEFAULT_STATE)
    merged.update(st)
    return merged


def save(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # keep only 30 days of realized history
    cutoff = time.time() - 30 * 86400
    state["realized"] = [r for r in state["realized"] if r["ts"] > cutoff]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, default=str)
    os.replace(tmp, path)


def realized_since(state, seconds):
    cutoff = time.time() - seconds
    return sum(r["usd"] for r in state["realized"] if r["ts"] > cutoff)
