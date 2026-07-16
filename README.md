# Setu 🌉 Perp-DEX funding arbitrage radar(This is not an investment advice.)

Scans Lighter, Aster, Paradex, Variational and Hyperliquid (public read-only
APIs, no keys), normalizes funding intervals, joins every venue pair on common
markets, and ranks delta-neutral funding spreads NET of fees + quoted spread.

**Columns that matter:** `gross_apr_%` (annualized funding spread) ·
`rt_cost_%` (entry+exit fees+spread, both legs) · `breakeven_days` (days of
funding to pay the costs) · `net_apr_Nd_%` (net if held N days) ·
`pair_liq_$` (min of OI / 24h vol across both legs) · `px_diverge_%`
(>2% = probably two different tokens with the same ticker - excluded).

**Unit calibration:** Setu auto-calibrates each venue's units against Hyperliquid on shared majors and
prints the chosen mode per venue. BEFORE trusting any big number: place a $50
test pair, watch ONE funding payment land, confirm it matches the dashboard.

**The three ways this loses money:** (1) one leg gets liquidated in a squeeze
and you're suddenly directional (at 3x a ~30% move kills a leg; at 5x ~19%);
(2) funding flips after you pay entry costs; (3) same ticker, different token.
Worst case per pair ~ one leg's margin. Not investment advice.

## 🤖 Autonomous bot

`bot/` turns the radar into a 24/7 self-trading bot: it scans every
`LOOP_MINUTES`, opens the best delta-neutral pair that passes all filters
(short the high-funding venue, long the low), accrues funding, and exits on
spread collapse / max hold / stop-loss / divergence — no intervention needed.

```bash
pip install -r requirements-bot.txt
python -m bot.main            # paper mode (default): simulated fills, real market data
python -m bot.main --status   # positions & PnL
touch data/KILL               # emergency stop: flatten everything, halt
```

**Modes.** Paper (default) simulates fills net of fees+spread across all 5
venues — safe, no keys. Live executes real orders on **Hyperliquid + Aster**
and requires both `MODE=live` and the explicit
`LIVE_CONFIRM=I_UNDERSTAND_LIVE_TRADING_RISKS` interlock plus API keys
(Hyperliquid agent wallet — trade-only, cannot withdraw; Aster trade-only
key, IP-whitelisted). See `.env.example` for every knob.

**Controls baked in:** max open pairs & total notional caps, per-pair
stop-loss, daily-loss halt, entry sanity caps (liquidity, price-match,
breakeven, APR ceiling for data glitches), cooldowns, one-legged-position
detection (closes the survivor immediately), error-streak halt, kill file,
Telegram alerts on every action + daily heartbeat.

**Deploy free:** paper mode runs serverless on GitHub Actions
(`.github/workflows/paper-bot.yml`, active once merged to main); live mode
belongs on an always-free VM (Oracle Cloud / GCP e2-micro) with Docker —
step-by-step in [DEPLOY.md](DEPLOY.md).

**PS**: This is not an investment advice.
