"""A thin Telegram Bot API client over HTTPS: only the calls the bot uses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 50


class TelegramError(RuntimeError):
    pass


@dataclass(frozen=True)
class Button:
    label: str
    url: str


class Messenger(Protocol):
    def send_message(
        self, chat_id: str, text: str, button: Button | None = None, topic: int | None = None
    ) -> None: ...

    def send_photo(
        self,
        chat_id: str,
        png: bytes,
        caption: str | None = None,
        button: Button | None = None,
        topic: int | None = None,
    ) -> int: ...

    def send_album(
        self,
        chat_id: str,
        pngs: tuple[bytes, ...],
        caption: str | None = None,
        topic: int | None = None,
    ) -> list[int]: ...

    def pin_message(self, chat_id: str, message_id: int) -> None: ...

    def unpin_message(self, chat_id: str, message_id: int) -> None: ...


class TelegramApi:
    def __init__(self, client: httpx.Client, token: str) -> None:
        self._client = client
        self._token = token

    def _call(self, method: str, *, http_timeout: float = 30, **fields: Any) -> Any:
        url = API.format(token=self._token, method=method)
        files = fields.pop("files", None)
        if files:
            response = self._client.post(url, data=fields, files=files, timeout=http_timeout)
        else:
            response = self._client.post(url, json=fields, timeout=http_timeout)
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramError(f"{method}: {payload.get('description', response.status_code)}")
        return payload["result"]

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        fields: dict[str, Any] = {"timeout": POLL_SECONDS, "allowed_updates": ["message"]}
        if offset is not None:
            fields["offset"] = offset
        result = self._call("getUpdates", http_timeout=POLL_SECONDS + 10, **fields)
        return list(result)

    def send_message(
        self, chat_id: str, text: str, button: Button | None = None, topic: int | None = None
    ) -> None:
        fields: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if button:
            fields["reply_markup"] = _keyboard(button)
        if topic is not None:
            fields["message_thread_id"] = topic
        self._call("sendMessage", **fields)

    def send_photo(
        self,
        chat_id: str,
        png: bytes,
        caption: str | None = None,
        button: Button | None = None,
        topic: int | None = None,
    ) -> int:
        fields: dict[str, Any] = {"chat_id": chat_id, "parse_mode": "HTML"}
        if caption:
            fields["caption"] = caption
        if button:
            fields["reply_markup"] = json.dumps(_keyboard(button))
        if topic is not None:
            fields["message_thread_id"] = topic
        result = self._call(
            "sendPhoto", http_timeout=60, files={"photo": ("card.png", png, "image/png")}, **fields
        )
        return int(result["message_id"])

    def send_album(
        self,
        chat_id: str,
        pngs: tuple[bytes, ...],
        caption: str | None = None,
        topic: int | None = None,
    ) -> list[int]:
        """Photos as one swipeable album; only the first photo's caption is shown."""
        media: list[dict[str, str]] = [
            {"type": "photo", "media": f"attach://card{index}"} for index in range(len(pngs))
        ]
        if caption:
            media[0] |= {"caption": caption, "parse_mode": "HTML"}
        fields: dict[str, Any] = {"chat_id": chat_id, "media": json.dumps(media)}
        if topic is not None:
            fields["message_thread_id"] = topic
        files = {
            f"card{index}": (f"card{index}.png", png, "image/png") for index, png in enumerate(pngs)
        }
        result = self._call("sendMediaGroup", http_timeout=90, files=files, **fields)
        return [int(message["message_id"]) for message in result]

    def pin_message(self, chat_id: str, message_id: int) -> None:
        self._call(
            "pinChatMessage", chat_id=chat_id, message_id=message_id, disable_notification=True
        )

    def unpin_message(self, chat_id: str, message_id: int) -> None:
        self._call("unpinChatMessage", chat_id=chat_id, message_id=message_id)

    def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        listed = [{"command": name, "description": text} for name, text in commands]
        self._call("setMyCommands", commands=listed)

    def username(self) -> str:
        return str(self._call("getMe")["username"])


def _keyboard(button: Button) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": button.label, "url": button.url}]]}
