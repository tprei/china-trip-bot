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
    def send_message(self, chat_id: str, text: str, button: Button | None = None) -> None: ...

    def send_photo(
        self, chat_id: str, png: bytes, caption: str | None = None, button: Button | None = None
    ) -> None: ...


class TelegramApi:
    def __init__(self, client: httpx.Client, token: str) -> None:
        self._client = client
        self._token = token

    def _call(self, method: str, timeout: float = 30, **fields: Any) -> Any:
        url = API.format(token=self._token, method=method)
        files = fields.pop("files", None)
        if files:
            response = self._client.post(url, data=fields, files=files, timeout=timeout)
        else:
            response = self._client.post(url, json=fields, timeout=timeout)
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramError(f"{method}: {payload.get('description', response.status_code)}")
        return payload["result"]

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        fields: dict[str, Any] = {"timeout": POLL_SECONDS, "allowed_updates": ["message"]}
        if offset is not None:
            fields["offset"] = offset
        result = self._call("getUpdates", timeout=POLL_SECONDS + 10, **fields)
        return list(result)

    def send_message(self, chat_id: str, text: str, button: Button | None = None) -> None:
        fields: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if button:
            fields["reply_markup"] = _keyboard(button)
        self._call("sendMessage", **fields)

    def send_photo(
        self, chat_id: str, png: bytes, caption: str | None = None, button: Button | None = None
    ) -> None:
        fields: dict[str, Any] = {"chat_id": chat_id, "parse_mode": "HTML"}
        if caption:
            fields["caption"] = caption
        if button:
            fields["reply_markup"] = json.dumps(_keyboard(button))
        self._call(
            "sendPhoto", timeout=60, files={"photo": ("card.png", png, "image/png")}, **fields
        )

    def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        listed = [{"command": name, "description": text} for name, text in commands]
        self._call("setMyCommands", commands=listed)

    def username(self) -> str:
        return str(self._call("getMe")["username"])


def _keyboard(button: Button) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": button.label, "url": button.url}]]}
