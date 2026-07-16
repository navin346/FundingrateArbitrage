"""Hyperliquid live executor via the official hyperliquid-python-sdk.

Security model: HL_PRIVATE_KEY should be an *agent (API) wallet* approved from
the main account — agent wallets can trade but can NOT withdraw funds. Set
HL_ACCOUNT_ADDRESS to the main account address the agent acts for.
Only core-dex perps are tradeable here (HIP-3 / xyz markets are skipped)."""
import logging

log = logging.getLogger("setu-bot")


class HyperliquidBroker:
    venue = "hyperliquid"

    def __init__(self, cfg):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        wallet = Account.from_key(cfg.hl_private_key)
        self.addr = cfg.hl_account_address or wallet.address
        self.info = Info(constants.MAINNET_API_URL, skip_ws=True)
        self.ex = Exchange(wallet, constants.MAINNET_API_URL, account_address=self.addr)
        meta = self.info.meta()
        self.sz_dec = {u["name"]: int(u["szDecimals"]) for u in meta["universe"]}

    def check(self):
        st = self.info.user_state(self.addr)
        return f"equity ${float(st['marginSummary']['accountValue']):,.2f}"

    @staticmethod
    def _parse_fill(res):
        if res.get("status") != "ok":
            raise RuntimeError(f"hyperliquid order rejected: {res}")
        statuses = res["response"]["data"]["statuses"]
        px, sz = 0.0, 0.0
        for s in statuses:
            if "error" in s:
                raise RuntimeError(f"hyperliquid order error: {s['error']}")
            f = s.get("filled")
            if f:
                fsz = float(f["totalSz"])
                px = (px * sz + float(f["avgPx"]) * fsz) / (sz + fsz) if sz + fsz else 0.0
                sz += fsz
        if sz <= 0:
            raise RuntimeError(f"hyperliquid order not filled: {res}")
        return {"px": px, "qty": sz}

    def open_leg(self, raw_sym, side, notional_usd, raw_px, leverage):
        if raw_sym not in self.sz_dec:
            raise RuntimeError(f"{raw_sym} not tradeable on hyperliquid core dex")
        try:
            self.ex.update_leverage(int(leverage), raw_sym)
        except Exception as e:
            log.warning("hyperliquid update_leverage(%s) failed: %s", raw_sym, e)
        sz = round(notional_usd / raw_px, self.sz_dec[raw_sym])
        if sz <= 0:
            raise RuntimeError(f"size rounds to 0 for {raw_sym} at ${notional_usd}")
        res = self.ex.market_open(raw_sym, side == "long", sz, None, 0.01)
        return self._parse_fill(res)

    def close_leg(self, raw_sym, qty, side, raw_px_hint):
        res = self.ex.market_close(raw_sym)
        if res is None:  # no position on the venue side
            return {"px": raw_px_hint}
        return self._parse_fill(res)

    def get_position(self, raw_sym):
        st = self.info.user_state(self.addr)
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            if p["coin"] == raw_sym:
                return float(p["szi"])
        return 0.0
