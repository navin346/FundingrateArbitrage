# Is funding arbitrage worth building a trading system around?

Hyperliquid x Aster, 60 liquid symbols, 351 days of hourly funding + prices
(2025-10-22 -> 2026-10-08). Funding is the amount actually paid; fees+spread use
the radar's cost model (0.26% round trip, both legs, at $600/leg).
Reproduce: `python -m backtest.fetch --days 365 --top 60 --out data/backtest365 && python -m backtest.study`

Selection is walk-forward: 3 anchored folds, settings picked on earlier data
only, then run untouched on the next window. Out-of-sample (OOS) span = 176 days.

## Verdict

* **Chasing funding spikes (what the bot did): no.** Not fixable with tuning; the spikes
  are gone before the fees are paid back.
* **Persistent-spread carry: a thin, fragile edge, not a business.** About +12% APR on
  3x-levered margin (+4% unlevered), 90% CI roughly +3%..+23% on margin; ~half of the profit
  comes from three small caps; it **disappears on liquid names** and shrinks to ~3.5% (levered)
  at fees that are realistic for larger size.
* Plain single-venue majors carry (long spot / short perp) paid ~5-6% on notional (~4.5% on
  capital) over the same year with far fewer moving parts, i.e. the complex system does not
  clearly beat the simple one.

## Numbers (OOS, 176 days)

| strategy | trades/periods | net PnL | APR on margin | notes |
|---|---|---|---|---|
| A spike-chaser as built | 652 trades | **-$714** | -124% | fees $1,017 vs funding $260 |
| B spike-chaser, patched* | 38 trades | +$91 | +16% | *rules chosen after seeing overlapping data: indicative only |
| C carry, full 60 symbols | 27 periods | **+$129** | **+12.5%** (+4.2% unlevered) | CI +3%..+23%; funding +$244, basis -$22, fees -$94 |
| C carry, top-20 by volume | 38 periods | -$5 | -0.8% | edge gone |
| C carry, top-10 majors | 27 periods | +$4 | +0.7% | edge gone |

Carry at different round-trip costs (settings re-selected each time):

| round trip cost | OOS net | APR on margin |
|---|---|---|
| 0.50% (larger size / illiquid alts) | +$40 | +3.5% |
| 0.26% (taker/taker, assumed) | +$129 | +12.5% |
| 0.18% | +$102 | +17.6% |
| 0.10% (mostly maker) | +$119 | +20.7% |
| 0.05% (optimistic) | +$125 | +21.7% |

Reference yield, single-venue carry on Hyperliquid majors (funding paid to a short perp, % APR on
notional): BTC 5.9, ETH 6.3, BNB 5.7, HYPE 9.4, SOL -0.2, XRP 2.9 (last 12 months; BTC/ETH ~9-10%
in the latest two quarters, 3.5-4% in the earliest).

## Why the answer is "thin"

1. **Spikes are not carry.** A >=100% APR spread lasts a median of 1 hour; 24h later the median spread
   is ~0. Funding actually paid over the next 24h is ~17% of what the headline APR implies, below
   the $1.56 round-trip fee (see the paper-bot post-mortem).
2. **Persistent gaps exist, but they sit in small, thin coins** (TRUMP, STRK, GRIFFAIN in the OOS
   window). On the top-20 / top-10 most liquid symbols the net edge is ~0 after fees. Thin coins
   are exactly where real-size slippage is worst and where capacity is smallest.
3. **Fees are the whole game.** Funding collected is $244 vs $94 of fees on the best settings;
   at 0.50% the strategy keeps only ~30% of its PnL.
4. **Small sample of independent decisions.** 27 weekly-ish periods; the bootstrap CI includes ~+1%
   unlevered. 324 settings were searched (walk-forward and the median-setting check limit, but do
   not remove, selection bias). Across all settings, 66-73% were profitable OOS on the full universe
   (median fold PnL +$10..+$14), but the median setting made ~$0 on the top-20.

## What this does not capture (all would make real results worse, not better)

Universe = top 60 by *today's* volume (survivor bias); two venues only (no Lighter / Paradex /
Variational history); no liquidity or open-interest history; no liquidation, auto-deleveraging,
venue-downtime, exchange-failure or withdrawal risk (both legs are 3x levered perps on young DEXes);
no funding-cap or delisting events beyond the sample; fills at hourly closes; $600/leg size only.

## Dollars

At the best-estimate +12.5% on $1,200 of margin the system earns ~$150/year; the CI's low end is
~$40/year. Capacity is limited by the thin coins that carry the edge. The engineering and
monitoring cost of a 24/7 system is far larger than that at this scale.
