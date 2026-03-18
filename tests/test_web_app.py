from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from unittest import TestCase
from urllib.parse import urlencode

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from bot.web_app import RequestError, TelegramInitDataVerifier


class TelegramInitDataVerifierTests(TestCase):
    def setUp(self) -> None:
        self._bot_token = "123456:TESTTOKEN"
        self._verifier = TelegramInitDataVerifier(bot_token=self._bot_token, ttl_seconds=3600)

    def test_verify_accepts_valid_payload(self) -> None:
        init_data = self._build_init_data(user_id=7001)

        auth = self._verifier.verify(init_data)

        self.assertEqual(auth.user_id, 7001)
        self.assertEqual(auth.chat_id, 7001)
        self.assertEqual(auth.username, "rita")

    def test_verify_rejects_expired_payload(self) -> None:
        auth_date = datetime.now(tz=UTC) - timedelta(hours=2)
        init_data = self._build_init_data(user_id=7001, auth_date=auth_date)

        with self.assertRaises(RequestError) as context:
            self._verifier.verify(init_data)

        self.assertEqual(context.exception.status, 401)

    def _build_init_data(self, user_id: int, auth_date: datetime | None = None) -> str:
        payload = {
            "auth_date": str(
                int((auth_date or datetime.now(tz=UTC)).timestamp())
            ),
            "query_id": "AAEAAAE",
            "user": json.dumps(
                {
                    "id": user_id,
                    "first_name": "Rita",
                    "username": "rita",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(payload.items(), key=lambda item: item[0])
        )
        secret_key = hmac.new(
            b"WebAppData",
            self._bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        payload["hash"] = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return urlencode(payload)
