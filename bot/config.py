"""Bot configuration. Everything comes from environment variables so the same
image runs in Docker, a VM, or GitHub Actions. Secrets are NEVER read from
files inside the repo and never logged."""
import os

LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_LIVE_TRADING_RISKS"


def _f(key, default):
    v = os.getenv(key, "").strip()
    return float(v) if v else float(default)


def _i(key, default):
    return int(_f(key, default))


def _s(key, default=""):
    return os.getenv(key, default).strip()


class Config:
    def __init__(self):
        # mode -------------------------------------------------------------
        self.mode = _s("MODE", "paper").lower()          # paper | live
        self.live_confirm = _s("LIVE_CONFIRM")
        self.loop_minutes = max(1.0, _f("LOOP_MINUTES", 5))

        # sizing -----------------------------------------------------------
        self.margin_per_leg = _f("MARGIN_PER_LEG_USD", 200)
        self.leverage = min(5.0, max(1.0, _f("LEVERAGE", 3)))
        self.notional_per_leg = self.margin_per_leg * self.leverage

        # entry filters ----------------------------------------------------
        self.min_entry_apr = _f("MIN_ENTRY_GROSS_APR", 100)     # % annualized
        self.max_entry_apr = _f("MAX_ENTRY_GROSS_APR", 3000)    # data-glitch guard
        self.min_pair_liq = _f("MIN_PAIR_LIQ_USD", 1_000_000)
        self.max_px_div_entry = _f("MAX_PX_DIVERGENCE_ENTRY_PCT", 1.0)
        self.max_breakeven_days = _f("MAX_BREAKEVEN_DAYS", 4)
        self.max_liq_fraction = _f("MAX_LIQ_FRACTION", 0.005)   # notional <= 0.5% pair liq
        self.entry_cooldown_h = _f("ENTRY_COOLDOWN_HOURS", 6)
        self.symbol_blacklist = {s.strip().upper() for s in _s("SYMBOL_BLACKLIST").split(",") if s.strip()}

        # exit rules ---------------------------------------------------------
        self.exit_apr = _f("EXIT_GROSS_APR", 20)                # % annualized
        self.exit_confirm_ticks = _i("EXIT_CONFIRM_TICKS", 2)
        self.max_px_div_exit = _f("MAX_PX_DIVERGENCE_EXIT_PCT", 3.0)
        self.max_hold_days = _f("MAX_HOLD_DAYS", 10)
        self.missing_data_ticks = _i("MISSING_DATA_TICKS", 3)

        # portfolio / circuit breakers --------------------------------------
        self.max_open_pairs = _i("MAX_OPEN_PAIRS", 3)
        self.max_total_notional = _f("MAX_TOTAL_NOTIONAL_USD", 5000)
        self.pair_stop_loss = _f("PAIR_STOP_LOSS_USD", 0.5 * self.margin_per_leg)
        self.daily_loss_limit = _f("DAILY_LOSS_LIMIT_USD", self.margin_per_leg)
        self.halt_hours_on_loss = _f("HALT_HOURS_ON_LOSS", 24)
        self.max_error_streak = _i("MAX_ERROR_STREAK", 5)
        self.halt_hours_on_errors = _f("HALT_HOURS_ON_ERRORS", 6)

        # files --------------------------------------------------------------
        self.data_dir = _s("DATA_DIR", "data")
        self.state_file = os.path.join(self.data_dir, "state.json")
        self.kill_file = os.path.join(self.data_dir, "KILL")

        # notifications -------------------------------------------------------
        self.tg_token = _s("TELEGRAM_BOT_TOKEN")
        self.tg_chat = _s("TELEGRAM_CHAT_ID")

        # live-trading credentials (only needed when MODE=live) ----------------
        self.hl_private_key = _s("HL_PRIVATE_KEY")        # use an *agent/API wallet* key
        self.hl_account_address = _s("HL_ACCOUNT_ADDRESS")  # main account the agent trades for
        self.aster_api_key = _s("ASTER_API_KEY")
        self.aster_api_secret = _s("ASTER_API_SECRET")

        # venues the live executors support (others stay scan-only)
        self.live_venues = {"hyperliquid", "aster"}

    @property
    def is_live(self):
        return self.mode == "live" and self.live_confirm == LIVE_CONFIRM_PHRASE

    def validate(self):
        if self.mode not in ("paper", "live"):
            raise SystemExit(f"MODE must be 'paper' or 'live', got '{self.mode}'")
        if self.mode == "live":
            if self.live_confirm != LIVE_CONFIRM_PHRASE:
                raise SystemExit(
                    "MODE=live requires LIVE_CONFIRM=" + LIVE_CONFIRM_PHRASE +
                    " — refusing to trade real funds without explicit confirmation.")
            missing = [k for k, v in (("HL_PRIVATE_KEY", self.hl_private_key),
                                      ("ASTER_API_KEY", self.aster_api_key),
                                      ("ASTER_API_SECRET", self.aster_api_secret)) if not v]
            if missing:
                raise SystemExit("MODE=live but missing credentials: " + ", ".join(missing))
