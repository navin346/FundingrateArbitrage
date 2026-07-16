"""Logging + optional Telegram push. Telegram failures never break the bot."""
import logging

import requests

log = logging.getLogger("setu-bot")


class Notifier:
    def __init__(self, cfg):
        self.token = cfg.tg_token
        self.chat = cfg.tg_chat

    def send(self, msg, level=logging.INFO):
        log.log(level, msg)
        if self.token and self.chat:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat, "text": msg[:4000]}, timeout=10)
            except Exception as e:
                log.warning("telegram notify failed: %s", e)

    def alert(self, msg):
        self.send("⚠️ " + msg, logging.WARNING)
