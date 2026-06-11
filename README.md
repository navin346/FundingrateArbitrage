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

**PS**: This is not an investment advice.
