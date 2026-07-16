"""Autonomous funding-arbitrage engine.

Each tick:
  1. kill switch  -> flatten everything and stop
  2. scan venues  -> funding APRs, marks, liquidity (from app.py radar)
  3. reconcile    -> live mode: verify both legs still exist on-venue;
                     a one-legged pair (liquidation) is closed immediately
  4. exits        -> spread collapsed, max hold, price divergence blowout,
                     per-pair stop loss, market delisted
  5. entries      -> top qualified spread if capacity + all filters pass;
                     short leg first, long leg second; if the hedge leg
                     fails the first leg is unwound immediately
  6. circuit breakers -> daily loss limit and error streak halt new entries

Positions are delta-neutral: short the venue paying high funding, long the
venue paying low, same asset, same notional."""
import logging
import time

import pandas as pd

from bot import state as st
from bot.brokers import build_brokers
from bot.notify import Notifier
from bot.scanner import scan

log = logging.getLogger("setu-bot")
HOURS_YEAR = 8760.0


def pair_key(sym, long_v, short_v):
    return f"{sym}|L:{long_v}|S:{short_v}"


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.notify = Notifier(cfg)
        self.brokers = build_brokers(cfg)
        self.state = st.load(cfg.state_file)

    # ---------------------------------------------------------------- utils
    def _save(self):
        st.save(self.cfg.state_file, self.state)

    def _halt(self, hours, reason):
        self.state["halt_until"] = max(self.state["halt_until"], time.time() + hours * 3600)
        self.state["halt_reason"] = reason
        self.notify.alert(f"entries halted {hours:.0f}h: {reason}")

    def _halted(self):
        return time.time() < self.state["halt_until"]

    def _gross_apr(self, rows, p):
        rl = rows.get((p["long_venue"], p["sym"]))
        rs = rows.get((p["short_venue"], p["sym"]))
        if rl is None or rs is None:
            return None, rl, rs
        return rs["apr"] - rl["apr"], rl, rs

    @staticmethod
    def _unrealized(p, pxl, pxs):
        """Basis PnL from raw fill prices + accrued funding - roundtrip costs."""
        basis = p["qty_long"] * (pxl - p["entry_px_long"]) + \
                p["qty_short"] * (p["entry_px_short"] - pxs)
        cost = p["notional"] * p["rt_cost_pct"] / 100.0
        return basis + p["funding_usd"] - cost

    # ---------------------------------------------------------------- legs
    def _close_pair(self, p, rows, reason):
        key = p["key"]
        rl = rows.get((p["long_venue"], p["sym"])) if rows else None
        rs = rows.get((p["short_venue"], p["sym"])) if rows else None
        pxl_hint = rl["mark"] * rl["mult"] if rl is not None else p["entry_px_long"]
        pxs_hint = rs["mark"] * rs["mult"] if rs is not None else p["entry_px_short"]
        errs = []
        pxl, pxs = pxl_hint, pxs_hint
        for leg, venue, raw, qty, side, hint in (
                ("long", p["long_venue"], p["raw_long"], p["qty_long"], "long", pxl_hint),
                ("short", p["short_venue"], p["raw_short"], p["qty_short"], "short", pxs_hint)):
            if p.get(f"closed_{leg}"):
                continue
            try:
                fill = self.brokers[venue].close_leg(raw, qty, side, hint)
                p[f"closed_{leg}"] = True
                if leg == "long":
                    pxl = fill["px"]
                else:
                    pxs = fill["px"]
            except Exception as e:
                errs.append(f"{leg}@{venue}: {e}")
        if errs:
            p["closing"] = True  # retry next tick
            self.notify.alert(f"close FAILED {key} ({reason}): {'; '.join(errs)[:300]}")
            return
        pnl = self._unrealized(p, pxl, pxs)
        self.state["realized"].append({"ts": time.time(), "key": key,
                                       "usd": round(pnl, 4), "reason": reason})
        self.state["cooldown"][key] = time.time()
        del self.state["pairs"][key]
        held_h = (time.time() - p["opened_ts"]) / 3600
        self.notify.send(f"CLOSED {key} after {held_h:.1f}h | reason={reason} | "
                         f"pnl ${pnl:+.2f} (funding ${p['funding_usd']:+.2f})")

    def flatten(self, reason):
        rows = {}
        try:
            rows, _, _ = scan(self.cfg)
        except Exception as e:
            log.warning("flatten: scan failed (%s), closing at entry prices", e)
        for p in list(self.state["pairs"].values()):
            self._close_pair(p, rows, reason)
        self._save()

    # ------------------------------------------------------------ reconcile
    def _reconcile(self):
        if not self.cfg.is_live:
            return
        for p in list(self.state["pairs"].values()):
            try:
                ql = self.brokers[p["long_venue"]].get_position(p["raw_long"])
                qs = self.brokers[p["short_venue"]].get_position(p["raw_short"])
            except Exception as e:
                log.warning("reconcile failed for %s: %s", p["key"], e)
                continue
            has_l, has_s = abs(ql or 0) > 1e-12, abs(qs or 0) > 1e-12
            if has_l and has_s:
                continue
            if not has_l and not has_s:
                self.notify.alert(f"{p['key']}: both legs gone on-venue (closed externally); dropping")
                del self.state["pairs"][p["key"]]
                continue
            # one-legged = directional exposure. Close the survivor NOW and halt.
            self.notify.alert(f"{p['key']}: ONE LEG MISSING (liquidated?) — closing survivor")
            p["closed_long"], p["closed_short"] = not has_l, not has_s
            self._close_pair(p, {}, "leg_missing")
            self._halt(self.cfg.halt_hours_on_loss, "one-legged pair detected")

    # ----------------------------------------------------------------- exits
    def _process_exits(self, rows, now):
        for p in list(self.state["pairs"].values()):
            if p.get("closing"):
                self._close_pair(p, rows, "retry_close")
                continue
            gross, rl, rs = self._gross_apr(rows, p)
            if gross is None:
                p["missing"] = p.get("missing", 0) + 1
                if p["missing"] >= self.cfg.missing_data_ticks:
                    self._close_pair(p, rows, "market_data_missing")
                continue
            p["missing"] = 0
            # accrue funding on elapsed time at current spread (paper + live estimate)
            hours = (now - p["last_accrual_ts"]) / 3600.0
            p["funding_usd"] += p["notional"] * (gross / 100.0) * (hours / HOURS_YEAR)
            p["last_accrual_ts"] = now
            pxl, pxs = rl["mark"] * rl["mult"], rs["mark"] * rs["mult"]
            unreal = self._unrealized(p, pxl, pxs)
            div = abs(rl["mark"] - rs["mark"]) / ((rl["mark"] + rs["mark"]) / 2) * 100
            held_days = (now - p["opened_ts"]) / 86400.0

            reason = None
            if unreal <= -self.cfg.pair_stop_loss:
                reason = f"stop_loss(${unreal:.2f})"
            elif div > self.cfg.max_px_div_exit:
                reason = f"px_divergence({div:.2f}%)"
            elif held_days >= self.cfg.max_hold_days:
                reason = "max_hold"
            elif gross < self.cfg.exit_apr:
                p["below_exit"] = p.get("below_exit", 0) + 1
                if p["below_exit"] >= self.cfg.exit_confirm_ticks:
                    reason = f"spread_collapsed({gross:.0f}%)"
            else:
                p["below_exit"] = 0
            if reason:
                self._close_pair(p, rows, reason)
            else:
                p["gross_apr_now"] = round(gross, 1)
                p["unrealized_usd"] = round(unreal, 2)

    # --------------------------------------------------------------- entries
    def _capacity_ok(self):
        open_notional = sum(2 * p["notional"] for p in self.state["pairs"].values())
        return (len(self.state["pairs"]) < self.cfg.max_open_pairs and
                open_notional + 2 * self.cfg.notional_per_leg <= self.cfg.max_total_notional)

    def _try_enter(self, rows, pairs, now):
        c = self.cfg
        if self._halted() or not self._capacity_ok() or not len(pairs):
            return
        open_syms = {p["sym"] for p in self.state["pairs"].values()}
        for _, r in pairs.iterrows():
            sym, long_v, short_v = r["sym"], r["long@"], r["short@"]
            key = pair_key(sym, long_v, short_v)
            liq = r["pair_liq_$"] or 0
            if (sym in c.symbol_blacklist or sym in open_syms
                    or long_v not in self.brokers or short_v not in self.brokers
                    or not (c.min_entry_apr <= r["gross_apr_%"] <= c.max_entry_apr)
                    or liq < c.min_pair_liq
                    or r["px_diverge_%"] > c.max_px_div_entry
                    or r["breakeven_days"] > c.max_breakeven_days
                    or c.notional_per_leg > c.max_liq_fraction * liq
                    or now - self.state["cooldown"].get(key, 0) < c.entry_cooldown_h * 3600):
                continue
            rl, rs = rows.get((long_v, sym)), rows.get((short_v, sym))
            if rl is None or rs is None:
                continue
            pxl, pxs = rl["mark"] * rl["mult"], rs["mark"] * rs["mult"]
            notional = c.notional_per_leg
            # short leg first (the funding receiver), then hedge with the long
            try:
                fs = self.brokers[short_v].open_leg(rs["raw"], "short", notional, pxs, c.leverage)
            except Exception as e:
                log.info("entry skip %s: short leg failed: %s", key, str(e)[:200])
                continue
            try:
                fl = self.brokers[long_v].open_leg(rl["raw"], "long", notional, pxl, c.leverage)
            except Exception as e:
                self.notify.alert(f"{key}: hedge leg failed ({str(e)[:150]}) — unwinding short leg")
                try:
                    self.brokers[short_v].close_leg(rs["raw"], fs["qty"], "short", pxs)
                except Exception as e2:
                    self.notify.alert(f"{key}: UNWIND FAILED, manual action needed: {e2}")
                    self._halt(c.halt_hours_on_loss, "unwind failure")
                self.state["err_streak"] += 1
                return
            self.state["pairs"][key] = {
                "key": key, "sym": sym, "long_venue": long_v, "short_venue": short_v,
                "raw_long": rl["raw"], "raw_short": rs["raw"],
                "qty_long": fl["qty"], "qty_short": fs["qty"], "notional": notional,
                "entry_px_long": fl["px"], "entry_px_short": fs["px"],
                "entry_gross_apr": float(r["gross_apr_%"]), "rt_cost_pct": float(r["rt_cost_%"]),
                "opened_ts": now, "last_accrual_ts": now, "funding_usd": 0.0,
                "below_exit": 0, "missing": 0,
            }
            self.notify.send(f"OPENED {key} | notional ${notional:,.0f}/leg @ {c.leverage:.0f}x | "
                             f"gross {r['gross_apr_%']:.0f}% APR, breakeven {r['breakeven_days']:.1f}d, "
                             f"liq ${liq:,.0f}")
            return  # max one new pair per tick

    # ------------------------------------------------------------------ tick
    def tick(self):
        now = time.time()
        import os
        if os.path.exists(self.cfg.kill_file):
            self.notify.alert("KILL file found — flattening all positions and stopping")
            self.flatten("kill_switch")
            return "kill"

        try:
            rows, pairs, errors = scan(self.cfg)
        except Exception as e:
            self.state["err_streak"] += 1
            log.error("scan failed (%d in a row): %s", self.state["err_streak"], e)
            if self.state["err_streak"] >= self.cfg.max_error_streak:
                self._halt(self.cfg.halt_hours_on_errors, f"{self.state['err_streak']} scan errors")
                self.state["err_streak"] = 0
            self._save()
            return "scan_error"
        if errors:
            log.warning("venue fetch errors: %s", errors)
        self.state["err_streak"] = 0

        self._reconcile()
        self._process_exits(rows, now)

        # daily-loss circuit breaker (exits above still ran; only entries stop)
        day_pnl = st.realized_since(self.state, 86400)
        if day_pnl <= -self.cfg.daily_loss_limit and not self._halted():
            self._halt(self.cfg.halt_hours_on_loss, f"daily loss ${day_pnl:.2f}")

        self._try_enter(rows, pairs, now)

        self.state["last_tick"] = now
        if now - self.state["last_heartbeat"] > 86400:
            self.state["last_heartbeat"] = now
            self.notify.send(self.status_line())
        self._save()
        return "ok"

    def status_line(self):
        pairs = self.state["pairs"]
        unreal = sum(p.get("unrealized_usd", 0) for p in pairs.values())
        d, w = st.realized_since(self.state, 86400), st.realized_since(self.state, 7 * 86400)
        halted = f" | HALTED until {time.strftime('%m-%d %H:%M', time.gmtime(self.state['halt_until']))}" \
            if self._halted() else ""
        detail = "; ".join(f"{k} {p.get('gross_apr_now', '?')}% ${p.get('unrealized_usd', 0):+.2f}"
                           for k, p in pairs.items()) or "none"
        return (f"[{self.cfg.mode}] open {len(pairs)}/{self.cfg.max_open_pairs} ({detail}) | "
                f"unreal ${unreal:+.2f} | realized 24h ${d:+.2f} / 7d ${w:+.2f}{halted}")
