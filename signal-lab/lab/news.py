"""Free news feeds -> themes -> per-asset scores.

Each THEME has keywords and a PRIOR: how we *guess* each asset reacts (+1 up, -1 down).
The bot then measures how assets actually moved after each theme (learn.py) and, once
there are enough events, replaces the guess with the measured reaction."""
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

H = {"User-Agent": "Mozilla/5.0 (signal-lab paper bot)"}


def gnews(q):
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}+when:1d&hl=en-US&gl=US&ceid=US:en"


FEEDS = {
    "Google News: bitcoin": gnews("bitcoin"),
    "Google News: crypto": gnews("ethereum OR solana OR crypto market"),
    "Google News: Fed / inflation": gnews("federal reserve OR FOMC OR inflation OR interest rates"),
    "Google News: oil": gnews("oil prices OR OPEC OR crude"),
    "Google News: gold": gnews("gold price"),
    "Google News: Nasdaq / tech": gnews("nasdaq OR tech stocks OR nvidia"),
    "Google News: geopolitics": gnews("war OR missile OR ceasefire OR sanctions"),
    "Google News: quantum": gnews("quantum computing bitcoin OR quantum encryption"),
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
}

CRYPTO = ("bitcoin", "btc", "crypto", "ether", "eth ", "solana", "sol ", "stablecoin", "blockchain", "token")

# theme: keywords (any), optional `needs` (any must also appear), prior reaction per asset
THEMES = {
    "fed_dovish":    {"kw": ["rate cut", "cuts rates", "cut rates", "dovish", "easing", "lower rates", "pause"],
                      "needs": ["fed", "fomc", "powell", "central bank", "rate"],
                      "prior": {"BTC": 1, "ETH": 1, "SOL": 1, "NASDAQ": 1, "GOLD": 1}},
    "fed_hawkish":   {"kw": ["rate hike", "hikes rates", "raise rates", "hawkish", "higher for longer", "tightening",
                             "hot inflation", "inflation accelerates", "inflation rises", "sticky inflation"],
                      "prior": {"BTC": -1, "ETH": -1, "SOL": -1, "NASDAQ": -1, "GOLD": -1}},
    "war_escalation": {"kw": ["missile", "invasion", "escalat", "airstrike", "air strike", "drone strike", "attack on",
                              "troops", "bombing", "declares war", "nuclear threat"],
                       "needs": ["military", "missile", "war", "troops", "strike", "russia", "ukraine", "iran", "israel",
                                 "china", "taiwan", "nato", "gaza", "houthi", "attack"],
                       "exclude": ["fed governor", "legal", "court", "lawsuit", "trade war"],
                       "prior": {"OIL": 1, "GOLD": 1, "BTC": -1, "ETH": -1, "SOL": -1, "NASDAQ": -1}},
    "ceasefire":     {"kw": ["ceasefire", "cease-fire", "peace deal", "truce", "peace talks", "peace agreement"],
                      "prior": {"OIL": -1, "GOLD": -1, "BTC": 1, "ETH": 1, "SOL": 1, "NASDAQ": 1}},
    "oil_supply_cut": {"kw": ["output cut", "production cut", "supply disruption", "strait of hormuz", "pipeline attack",
                              "inventories fall", "inventory draw", "opec+ cut"],
                       "prior": {"OIL": 1}},
    "oil_supply_up": {"kw": ["output hike", "raise output", "boost production", "increase output", "inventories rise",
                             "crude glut", "oversupply", "inventory build"],
                      "prior": {"OIL": -1}},
    "tariffs_sanctions": {"kw": ["tariff", "trade war", "sanction", "export ban", "export controls"],
                          "exclude": ["ease", "eases", "lift", "lifts", "lifted", "relief", "reopen", "roll back", "pause"],
                          "prior": {"NASDAQ": -1, "BTC": -1, "ETH": -1, "SOL": -1, "GOLD": 1}},
    "crypto_bull":   {"kw": ["etf inflow", "inflows", "etf approval", "approves", "record high", "all-time high",
                             "treasury buys", "adds bitcoin", "adoption", "rally", "surges", "soars", "breakout"],
                      "needs": CRYPTO, "prior": {"BTC": 1, "ETH": 1, "SOL": 1}},
    "crypto_bear":   {"kw": ["hack", "exploit", "outflow", "ban", "crackdown", "lawsuit", "sues", "liquidation",
                             "plunge", "crash", "sell-off", "selloff", "tumbles", "slumps"],
                      "exclude": ["volatility has plunged", "one year after", "anniversary", "years after", "rebound"],
                      "needs": CRYPTO, "prior": {"BTC": -1, "ETH": -1, "SOL": -1}},
    "quantum_threat": {"kw": ["quantum"], "needs": ["bitcoin", "crypto", "encryption", "private key", "wallet"],
                       "prior": {"BTC": -1, "ETH": -1}},
    "tech_strong":   {"kw": ["beats estimates", "earnings beat", "record revenue", "ai boom", "raises guidance",
                             "blowout quarter"], "prior": {"NASDAQ": 1}},
    "tech_weak":     {"kw": ["misses estimates", "earnings miss", "cuts guidance", "guidance cut", "layoffs",
                             "antitrust"], "prior": {"NASDAQ": -1}},
    "gold_bid":      {"kw": ["safe haven", "safe-haven", "central bank gold", "gold buying", "gold record"],
                      "prior": {"GOLD": 1}},
}


def _text(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None else ""


def fetch_headlines(max_age_h=36):
    items, errors = [], {}
    now = time.time()
    for src, url in FEEDS.items():
        try:
            r = requests.get(url, headers=H, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = re.sub(r"\s+", " ", _text(it, "title"))
                if not title:
                    continue
                try:
                    ts = parsedate_to_datetime(_text(it, "pubDate")).timestamp()
                except (TypeError, ValueError):
                    ts = now
                if now - ts > max_age_h * 3600:
                    continue
                key = hashlib.sha1(title.lower()[:90].encode()).hexdigest()[:16]
                items.append({"id": key, "ts": ts, "title": title, "source": src, "link": _text(it, "link")})
        except Exception as e:                         # a dead feed must never break the bot
            errors[src] = str(e)[:120]
    uniq = {}
    for it in sorted(items, key=lambda x: x["ts"]):
        uniq.setdefault(it["id"], it)
    return list(uniq.values()), errors


def tag(title):
    """Return the themes a headline matches."""
    t = " " + title.lower() + " "
    hits = []
    for name, th in THEMES.items():
        if (any(k in t for k in th["kw"]) and (not th.get("needs") or any(n in t for n in th["needs"]))
                and not any(x in t for x in th.get("exclude", []))):
            hits.append(name)
    return hits


def effect(theme, asset, matrix):
    """Learned reaction if we have enough evidence, else the prior guess."""
    cell = (matrix.get(theme) or {}).get(asset)
    from lab.config import MATRIX_MIN_EVENTS
    if cell and cell["n"] >= MATRIX_MIN_EVENTS:
        m, sd, n = cell["mean_24h"], cell["sd_24h"], cell["n"]
        t = m / (sd / n ** 0.5) if sd > 0 else (float("inf") if m != 0 else 0.0)
        return (1 if m > 0 else -1) if abs(t) >= 1.0 else 0      # weak evidence -> no opinion
    return THEMES[theme]["prior"].get(asset, 0)
