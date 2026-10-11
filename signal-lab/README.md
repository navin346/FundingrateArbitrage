# Signal Lab

**Which trading signals actually beat a coin flip?** A paper-trading research bot that runs every hour,
for free, on GitHub Actions, and publishes a live dashboard on GitHub Pages. **Paper only: no keys, no real orders.**

## What it does
Every hour it reads prices, funding and open interest from Hyperliquid's public API for
**Bitcoin, Ether, Solana, Gold, WTI crude and the Nasdaq-100**, plus headlines from Google News,
CoinDesk, Cointelegraph and the Federal Reserve. Eight strategies trade side by side, each with
its own virtual **$500** at **3x**, with a **2% stop-loss / 2% take-profit** checked on every hourly candle.
Fees, slippage and funding are charged.

| Strategy | Idea |
|---|---|
| Trend 3d / Trend 7d | go with the recent trend |
| Follow crowd / Fade crowd | funding far above/below baseline = crowded longs/shorts; follow or fade them |
| News | headline themes (Fed, war, ceasefire, oil supply, tariffs, crypto, quantum, tech earnings) |
| Combo (self-learning) | weighted vote of the above; weights = each signal's live win-rate edge |
| Coin flip, Always long | **controls**: a signal only matters if it beats both, over 100+ trades |

**Learning loop.** Every news theme is logged with the prices at that moment; 4h and 24h later the bot
records how each asset actually moved. That builds the *news -> asset reaction matrix* on the dashboard.
Once a cell has 10+ events, the News strategy trusts the **measured** reaction instead of the starting
guess (and ignores the cell if the evidence is weak). The Combo strategy re-weights signals by results.

## Run locally
```bash
cd signal-lab
pip install -r requirements.txt
python tests/test_core.py          # accounting + learning tests
python -m lab.main --reset         # fresh state, first tick
cd docs && python -m http.server   # open http://localhost:8000
```
Settings live in `lab/config.py`.

**Live dashboard:** https://navin346.github.io/FundingrateArbitrage/

## Deploy (already wired)
* `.github/workflows/signal-lab.yml` (repo root) runs hourly and force-pushes `docs/` (dashboard + `data/`) to the
  `gh-pages` branch (one commit, so the repo never grows).
* Settings -> Pages -> Source: **Deploy from a branch**, branch **gh-pages**, folder **/ (root)**.
* The published folder carries a `vercel.json` that disables Vercel builds for `gh-pages`, so hourly
  pushes don't burn the Vercel deployment quota of the main dashboard.
* GitHub pauses scheduled workflows after 60 days without activity on the repo; if the dashboard stops
  updating, re-enable it in the Actions tab or push any commit.

## Honest limits
Headline tagging is keyword-based (a v2 can use an LLM classifier); one story often produces many
headlines; GitHub's hourly schedule can be delayed; stops are checked on hourly highs/lows, not ticks.
Early results are noise: judge only after 100+ trades per strategy. Not investment advice.
