"""Simulated broker: fills at the venue's mark price. Explicit trading costs
(fees + spread) are charged by the engine from the scanner's rt_cost model,
so the paper track record is net of realistic costs."""


class PaperBroker:
    def __init__(self, venue):
        self.venue = venue

    def check(self):
        return "paper"

    def open_leg(self, raw_sym, side, notional_usd, raw_px, leverage):
        qty = notional_usd / raw_px
        return {"px": raw_px, "qty": qty}

    def close_leg(self, raw_sym, qty, side, raw_px_hint):
        return {"px": raw_px_hint}

    def get_position(self, raw_sym):
        return None  # engine trusts its own state in paper mode
