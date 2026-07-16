"""Entry point.

  python -m bot.main            # run forever (default paper mode)
  python -m bot.main --once     # single tick (for cron / GitHub Actions)
  python -m bot.main --status   # print current positions & PnL
  python -m bot.main --flatten  # close everything now and exit

Emergency stop while running: create the kill file (default data/KILL) —
next tick the bot flattens all positions and stops."""
import argparse
import logging
import signal
import sys
import time

from bot.config import Config
from bot.engine import Engine

log = logging.getLogger("setu-bot")


def setup_logging():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")


def main():
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one tick and exit")
    ap.add_argument("--status", action="store_true", help="print status and exit")
    ap.add_argument("--flatten", action="store_true", help="close all positions and exit")
    args = ap.parse_args()

    cfg = Config()
    cfg.validate()
    eng = Engine(cfg)

    if args.status:
        print(eng.status_line())
        return

    mode = "LIVE — REAL FUNDS" if cfg.is_live else "PAPER (simulated)"
    log.info("Setu bot starting | mode=%s | %.0f USD margin x %.0fx per leg | "
             "max %d pairs / $%.0f total notional | loop %.0f min",
             mode, cfg.margin_per_leg, cfg.leverage, cfg.max_open_pairs,
             cfg.max_total_notional, cfg.loop_minutes)
    for venue, broker in eng.brokers.items():
        try:
            log.info("broker %s: %s", venue, broker.check())
        except Exception as e:
            raise SystemExit(f"broker {venue} failed startup check: {e}")

    if args.flatten:
        eng.flatten("manual_flatten")
        print(eng.status_line())
        return

    stop = {"now": False}

    def _sig(_s, _f):
        stop["now"] = True
        log.info("signal received, finishing tick then exiting (positions stay open)")

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    while True:
        try:
            result = eng.tick()
            log.info(eng.status_line())
            if result == "kill":
                break
        except Exception:
            log.exception("tick crashed")
            eng.state["err_streak"] = eng.state.get("err_streak", 0) + 1
        if args.once or stop["now"]:
            break
        time.sleep(cfg.loop_minutes * 60)


if __name__ == "__main__":
    main()
