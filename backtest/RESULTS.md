# Backtest results (Hyperliquid x Aster, 2026-06-13 -> 2026-10-08, 117 days)

Reproduce: `python -m backtest.fetch --days 120 --top 60 && python -m backtest.run`
(60 most liquid symbols listed on both venues; $200 margin x 3 = $600/leg; capital = 3 pairs x 2 legs x $200 = $1,200).
Funding PnL uses funding actually paid; fees+spread use the radar's cost model (0.26% per round trip).

## 1. The bot's current default settings lose money

| | trades | win rate | net PnL | funding | basis | costs | avg hold |
|---|---|---|---|---|---|---|---|
| full 117d | 482 | 29.5% | **-$551** | +$167 | +$34 | -$752 | 7.6 h |
| train (first 2/3) | 250 | 36.0% | -$257 | | | | |
| test (last 1/3) | 231 | 22.5% | -$289 | | | | |

The strategy finds real funding spreads, but enters on *instantaneous* spikes that collapse within hours
(474 of 482 exits were `spread_collapsed`, median hold under 8 h). Each round trip costs ~$1.56; the
funding collected in that short time does not repay it. This matches the live paper run, which was
-$12.94 over its last 7 days.

## 2. Trading less is consistently better, but the edge is small

Grid of 486 settings, scored on the first 2/3, then re-run untouched on the last 1/3.
Mean OUT-OF-SAMPLE PnL (39 days, $1,200 capital) by setting, averaged over all other settings:

| knob | worst value | best value |
|---|---|---|
| entry signal smoothing | none: -$12.5 (50 trades) | 72 h average: +$9.0 (3 trades) |
| min hold before collapse-exit | 0 h: -$12.5 | 72 h: +$16.6 |
| collapse confirmations | 2: -$16.2 | 12: +$15.6 |
| exit when spread below | 50% APR: -$6.8 | 0% (i.e. only when it flips): +$10.5 |

Every cost-reducing setting helps; train and test PnL correlate at 0.56 across configs. The best
configs (long holds, smoothed signals) return roughly +$10 to +$70 out-of-sample, i.e. about 5-55% APR
on capital, vs. -225% for the defaults. 60% of configs with enough trades were profitable out-of-sample;
the median was +$6 (about 5% APR).

## 3. What this does NOT show

* **No proven edge.** Best in-sample config made +72% APR; the same config made +9% out-of-sample.
  Treat anything above ~10% APR as unproven.
* One regime (4 months), one venue pair, 60 symbols chosen with today's volume (survivor bias).
* No historical liquidity/OI, no liquidation model, no funding-rate-flip tail events beyond this window.
* Hourly ticks; costs assume taker fills at fixed slippage guesses (3/6 bps). Maker fills would
  cut the dominant cost; real slippage on small caps could exceed the guess.
* Lighter / Paradex / Variational are not included (no usable price history), though most of the
  live paper trades used Lighter.
