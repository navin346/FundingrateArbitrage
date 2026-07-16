"""Broker registry. Paper mode gets a simulated broker for every venue;
live mode only wires venues with a real executor (hyperliquid, aster) —
the engine simply never enters a pair whose venues lack a broker."""
from bot.brokers.paper import PaperBroker

ALL_VENUES = ("hyperliquid", "lighter", "aster", "paradex", "variational")


def build_brokers(cfg):
    if cfg.is_live:
        from bot.brokers.aster_live import AsterBroker
        from bot.brokers.hyperliquid_live import HyperliquidBroker
        return {"hyperliquid": HyperliquidBroker(cfg), "aster": AsterBroker(cfg)}
    return {v: PaperBroker(v) for v in ALL_VENUES}
