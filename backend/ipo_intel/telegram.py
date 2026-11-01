"""Sending Telegram messages through the bot API (via the shared ApiClient)."""

from __future__ import annotations

from collections.abc import Mapping

from ipo_intel.clients.base import ApiClient, ApiError


class TelegramError(ApiError):
    """Telegram answered, but not with ok: true."""


class Telegram:
    def __init__(self, client: ApiClient, chat_id: str) -> None:
        self._client = client
        self.chat_id = chat_id

    def send(self, text: str, *, purpose: str = "alert") -> None:
        body = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        answer = self._client.request_json("POST", "/sendMessage", json_body=body, purpose=purpose)
        if not isinstance(answer, Mapping) or answer.get("ok") is not True:
            description = answer.get("description") if isinstance(answer, Mapping) else None
            raise TelegramError(
                f"telegram sendMessage failed: {description or 'unexpected answer'}",
                source=self._client.source,
                endpoint="/sendMessage",
            )

    def chats(self) -> list[dict]:
        """Chats that have messaged the bot recently (GET /getUpdates), to find TELEGRAM_CHAT_ID."""
        answer = self._client.get_json("/getUpdates", purpose="setup")
        found: dict[int, dict] = {}
        for update in answer.get("result", []) if isinstance(answer, Mapping) else []:
            message = update.get("message") or update.get("channel_post") or {}
            chat = message.get("chat") if isinstance(message, Mapping) else None
            if isinstance(chat, Mapping) and "id" in chat:
                name = chat.get("title") or " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
                found[chat["id"]] = {"id": chat["id"], "type": chat.get("type"), "name": name or chat.get("username")}
        return list(found.values())
