from __future__ import annotations

import logging

import httpx

from quran_video.config.logging import redact_secret_text

LOGGER = logging.getLogger(__name__)
MARKDOWN_V2_SPECIAL = set("_*[]()~`>#+-=|{}.!")


def escape_markdown_v2(value: str) -> str:
    return "".join(f"\\{char}" if char in MARKDOWN_V2_SPECIAL else char for char in value)


class TelegramClient:
    def __init__(
        self, bot_token: str, chat_id: str, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def send_message(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = await self.http.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": escape_markdown_v2(text),
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as error:
            LOGGER.error("telegram notification failed: %s", redact_secret_text(str(error)))
