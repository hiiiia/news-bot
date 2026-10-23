"""텔레그램 전송."""
from __future__ import annotations

import html
import time

import requests

from .sources import Item

CATEGORY_LABEL = {"security": "보안", "it": "IT", "ai": "AI"}


class TelegramError(RuntimeError):
    pass


def format_item(item: Item) -> str:
    icon = "🚨" if item.urgent else "•"
    label = item.label or CATEGORY_LABEL.get(item.category, item.category)
    title = html.escape(item.title[:300])
    meta = html.escape(item.source + (f" · {item.extra}" if item.extra else ""))
    url = html.escape(item.url, quote=True)
    summary = f"\n└ {html.escape(item.summary[:200])}" if item.summary else ""
    return f"{icon} <b>[{html.escape(label)}]</b> {title}{summary}\n<i>{meta}</i>\n{url}"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, text: str) -> None:
        for attempt in range(2):
            resp = requests.post(
                self.url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=20,
            )
            if resp.status_code == 429 and attempt == 0:
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(min(int(wait), 30))
                continue
            if not resp.ok:
                # 토큰이 들어간 URL은 오류 메시지에 포함하지 않는다.
                raise TelegramError(f"telegram {resp.status_code}: {resp.text[:200]}")
            return
