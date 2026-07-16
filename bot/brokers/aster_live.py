"""Aster live executor. Aster's fapi is Binance-futures-compatible:
HMAC-SHA256 signed requests with X-MBX-APIKEY header.

Security: create the API key with *trading only* (no withdrawal permission)
and IP-whitelist it to your server."""
import hashlib
import hmac
import logging
import math
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger("setu-bot")
BASE = "https://fapi.asterdex.com"


class AsterBroker:
    venue = "aster"

    def __init__(self, cfg):
        self.key = cfg.aster_api_key
        self.secret = cfg.aster_api_secret.encode()
        info = requests.get(BASE + "/fapi/v1/exchangeInfo", timeout=20).json()
        self.filters = {}
        for s in info.get("symbols", []):
            f = {x["filterType"]: x for x in s.get("filters", [])}
            self.filters[s["symbol"]] = {
                "step": float(f.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                "min_qty": float(f.get("LOT_SIZE", {}).get("minQty", 0)),
                "min_notional": float(f.get("MIN_NOTIONAL", {}).get("notional", 5)),
            }

    def _signed(self, method, path, params):
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        qs = urlencode(params)
        sig = hmac.new(self.secret, qs.encode(), hashlib.sha256).hexdigest()
        r = requests.request(method, f"{BASE}{path}?{qs}&signature={sig}",
                             headers={"X-MBX-APIKEY": self.key}, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"aster {path} {r.status_code}: {r.text[:200]}")
        return r.json()

    def check(self):
        bals = self._signed("GET", "/fapi/v2/balance", {})
        usdt = next((b for b in bals if b.get("asset") in ("USDT", "USDF")), {})
        return f"balance {usdt.get('asset', '?')} {float(usdt.get('balance', 0)):,.2f}"

    def _round_qty(self, symbol, qty):
        f = self.filters.get(symbol)
        if not f:
            raise RuntimeError(f"{symbol} not tradeable on aster")
        step = f["step"] or 0.001
        qty = math.floor(qty / step) * step
        qty = round(qty, 10)
        if qty < f["min_qty"] or qty <= 0:
            raise RuntimeError(f"qty {qty} below aster minQty for {symbol}")
        return qty

    @staticmethod
    def _fill_px(order):
        px = float(order.get("avgPrice") or 0)
        if px <= 0:
            ex_qty = float(order.get("executedQty") or 0)
            px = float(order.get("cumQuote") or 0) / ex_qty if ex_qty else 0
        if px <= 0 or float(order.get("executedQty") or 0) <= 0:
            raise RuntimeError(f"aster order not filled: {order}")
        return px

    def open_leg(self, raw_sym, side, notional_usd, raw_px, leverage):
        try:
            self._signed("POST", "/fapi/v1/leverage",
                         {"symbol": raw_sym, "leverage": int(leverage)})
        except Exception as e:
            log.warning("aster set leverage(%s) failed: %s", raw_sym, e)
        qty = self._round_qty(raw_sym, notional_usd / raw_px)
        f = self.filters[raw_sym]
        if qty * raw_px < f["min_notional"]:
            raise RuntimeError(f"notional below aster minimum for {raw_sym}")
        order = self._signed("POST", "/fapi/v1/order", {
            "symbol": raw_sym, "side": "BUY" if side == "long" else "SELL",
            "type": "MARKET", "quantity": qty, "newOrderRespType": "RESULT"})
        return {"px": self._fill_px(order), "qty": float(order["executedQty"])}

    def close_leg(self, raw_sym, qty, side, raw_px_hint):
        amt = self.get_position(raw_sym)
        if not amt:
            return {"px": raw_px_hint}
        order = self._signed("POST", "/fapi/v1/order", {
            "symbol": raw_sym, "side": "SELL" if amt > 0 else "BUY",
            "type": "MARKET", "quantity": abs(amt), "reduceOnly": "true",
            "newOrderRespType": "RESULT"})
        return {"px": self._fill_px(order)}

    def get_position(self, raw_sym):
        pos = self._signed("GET", "/fapi/v2/positionRisk", {"symbol": raw_sym})
        for p in pos if isinstance(pos, list) else [pos]:
            if p.get("symbol") == raw_sym:
                return float(p.get("positionAmt") or 0)
        return 0.0
