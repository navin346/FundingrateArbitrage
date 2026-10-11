"""All tunables in one place. Paper trading only: no keys, no real orders."""

# Hyperliquid perps. "xyz:" markets are the trade.xyz HIP-3 dex (gold, oil, Nasdaq-100).
# fee_bps = taker fee per side; xyz markets charge 2x the core fee.
ASSETS = {
    "BTC":    {"coin": "BTC",        "label": "Bitcoin",      "group": "crypto", "fee_bps": 4.5},
    "ETH":    {"coin": "ETH",        "label": "Ether",        "group": "crypto", "fee_bps": 4.5},
    "SOL":    {"coin": "SOL",        "label": "Solana",       "group": "crypto", "fee_bps": 4.5},
    "GOLD":   {"coin": "xyz:GOLD",   "label": "Gold",         "group": "metal",  "fee_bps": 9.0},
    "OIL":    {"coin": "xyz:CL",     "label": "WTI crude",    "group": "energy", "fee_bps": 9.0},
    "NASDAQ": {"coin": "xyz:XYZ100", "label": "Nasdaq-100",   "group": "equity", "fee_bps": 9.0},
}

START_EQUITY = 500.0        # virtual dollars per strategy
LEVERAGE = 3.0              # total notional = equity x LEVERAGE, split evenly across assets
STOP_PCT = 2.0              # stop-loss distance from entry, % of price
TARGET_PCT = 2.0            # take-profit distance from entry, % of price
SLIPPAGE_BPS = 2.0          # per side, on top of the taker fee
DECISION_HOUR_UTC = 0       # strategies re-decide once per day, at the first run after this hour
HL_BASELINE_FUNDING_APR = 10.95   # Hyperliquid's neutral funding (0.00125%/h) annualised

# signal thresholds
MOMENTUM_MIN_MOVE_PCT = 0.5       # ignore trends smaller than this
CROWDING_APR = 8.0                # funding this far above/below baseline = crowded
NEWS_MIN_SCORE = 2.0              # need at least this net news score to act
NEWS_LOOKBACK_H = 24

# self-learning
COMBO_WARMUP_TRADES = 15          # below this, a component gets equal weight
COMBO_WINDOW = 40                 # judge components on their last N closed trades
MATRIX_MIN_EVENTS = 10            # news reaction matrix overrides the prior after N events
