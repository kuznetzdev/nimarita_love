from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from nimarita.config import Settings


@dataclass(slots=True, frozen=True)
class InviteLinks:
    bot_start_link: str
    mini_app_link: str | None


class LinkBuilder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build_invite_links(self, raw_token: str) -> InviteLinks:
        start_payload = f"invite_{raw_token}"
        bot_start_link = f"https://t.me/{self._settings.bot_username}?start={quote(start_payload)}"

        mini_app_link: str | None = None
        if self._settings.mini_app_short_name:
            mini_app_link = (
                f"https://t.me/{self._settings.bot_username}/{self._settings.mini_app_short_name}"
                f"?startapp={quote(start_payload)}"
            )
        elif self._settings.bot_username:
            mini_app_link = f"https://t.me/{self._settings.bot_username}?startapp={quote(start_payload)}"

        return InviteLinks(bot_start_link=bot_start_link, mini_app_link=mini_app_link)
