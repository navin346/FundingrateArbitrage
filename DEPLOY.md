# Deploying the Setu bot 24/7 for free

Two free paths, by mode:

| Mode | Where | Why |
|---|---|---|
| **Paper** (default) | GitHub Actions (zero servers) | cron tick every 30 min, state cached, nothing to manage |
| **Live** | Oracle Cloud Always Free VM (or GCP e2-micro) | real positions need a machine that is always on and never skips a beat |

Start in paper for 1–2 weeks. Only go live once the simulated PnL and the
funding payments it predicts match what you can verify on the venues.

---

## Option A — Paper mode on GitHub Actions (zero infra)

Already wired: `.github/workflows/paper-bot.yml`.

1. Merge this branch to `main` (schedules only run from the default branch).
2. Optional: repo → Settings → Secrets and variables → Actions → add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to get pinged on every
   simulated entry/exit.
3. Watch runs in the Actions tab; each run prints the status line
   (open pairs, unrealized, realized 24h/7d).

Notes: Actions cron is best-effort (ticks can be delayed) — fine for paper,
**never** for live. GitHub pauses schedules after 60 days without commits;
re-enable in the Actions tab if that happens.

## Option B — Live (or paper) on Oracle Cloud Always Free

Oracle's Always Free tier includes an ARM VM (up to 4 cores / 24 GB) that is
free forever — the best home for a 24/7 trading loop. GCP's `e2-micro`
(us-central1/west1/east1) works the same way.

```bash
# 1. Create the VM (Ubuntu 22.04+), SSH in, install docker
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

# 2. Get the code and configure
git clone https://github.com/navin346/FundingrateArbitrage.git && cd FundingrateArbitrage
cp .env.example .env
nano .env            # set sizing/limits; keys only when going live

# 3. Run — restart: unless-stopped means it survives reboots and crashes
docker compose up -d --build
docker logs -f setu-bot
```

Everyday operations:

```bash
docker exec setu-bot python -m bot.main --status   # positions & PnL
touch data/KILL                                    # EMERGENCY STOP: flatten all, halt
rm data/KILL && docker restart setu-bot            # resume after a kill
docker compose down                                # stop (positions stay open on venues)
```

### Going live — security checklist

1. **Paper first.** Compare a week of paper PnL against real funding prints.
2. **Hyperliquid:** create an **agent/API wallet** (app.hyperliquid.xyz → API).
   Agent keys can trade but **cannot withdraw**. Never put your main wallet
   key in `.env`.
3. **Aster:** API key with **trade-only** permission, **IP-whitelisted** to
   the VM's public IP.
4. Fund the venues only with what the bot may use
   (`MAX_TOTAL_NOTIONAL_USD / LEVERAGE` plus buffer) — the account is the
   ultimate position limit.
5. `.env` is git-ignored; keep it `chmod 600 .env`. Lock the VM down:
   SSH keys only, no password auth, firewall allows outbound only + SSH.
6. Set the Telegram vars — the bot notifies every open/close/halt and a daily
   heartbeat, so "no monitoring" still leaves an audit trail on your phone.
7. Start small (`MARGIN_PER_LEG_USD=50`, `MAX_OPEN_PAIRS=1`) and scale after
   the first funding payments verifiably land.

### What the bot will do on its own

- scan all 5 venues every `LOOP_MINUTES`, enter the best spread that passes
  every filter (APR, liquidity, price-match, breakeven, cooldown, capacity)
- short the high-funding venue / long the low-funding venue, equal notional
  (live execution: Hyperliquid + Aster pairs; other venues are scan-only)
- exit on: spread collapse, max hold, per-pair stop-loss, price divergence,
  delisting — and immediately close the survivor if a leg ever goes missing
- halt new entries after a daily loss limit or repeated errors; kill-file
  flattens everything

### What it will NOT protect you from

Funding flipping right after entry (bounded by stop-loss + exit rule), venue
downtime while you hold a position there, smart-contract/venue insolvency
risk, or a wrong unit-calibration on an exotic venue (bounded by
`MAX_ENTRY_GROSS_APR` sanity cap and the $-small sizing you start with).
Worst case per pair ≈ one leg's margin. Not investment advice.
