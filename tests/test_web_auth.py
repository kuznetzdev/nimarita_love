from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import quote

from nimarita.web.auth import TelegramInitDataVerifier, WebAuthError


class TelegramInitDataVerifierTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.bot_token = '123:TEST'
        self.verifier = TelegramInitDataVerifier(bot_token=self.bot_token, ttl_seconds=3600)

    def test_verify_accepts_valid_payload(self) -> None:
        snapshot = self.verifier.verify(
            self._build_init_data(
                [
                    ('auth_date', str(int(time.time()))),
                    (
                        'user',
                        json.dumps(
                            {
                                'id': 101,
                                'username': 'alice',
                                'first_name': 'Alice',
                                'language_code': 'ru',
                            },
                            separators=(',', ':'),
                            ensure_ascii=False,
                        ),
                    ),
                ]
            )
        )

        self.assertEqual(snapshot.telegram_user_id, 101)
        self.assertEqual(snapshot.username, 'alice')
        self.assertEqual(snapshot.first_name, 'Alice')

    def test_verify_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(WebAuthError) as ctx:
            self.verifier.verify(
                self._build_init_data(
                    [
                        ('auth_date', str(int(time.time()))),
                        ('auth_date', str(int(time.time()))),
                        (
                            'user',
                            json.dumps(
                                {
                                    'id': 101,
                                    'username': 'alice',
                                    'first_name': 'Alice',
                                },
                                separators=(',', ':'),
                                ensure_ascii=False,
                            ),
                        ),
                    ]
                )
            )

        self.assertEqual(ctx.exception.status, 401)

    def test_verify_rejects_malformed_user_payload(self) -> None:
        with self.assertRaises(WebAuthError) as ctx:
            self.verifier.verify(
                self._build_init_data(
                    [
                        ('auth_date', str(int(time.time()))),
                        ('user', '{'),
                    ]
                )
            )

        self.assertEqual(ctx.exception.status, 401)

    def _build_init_data(self, pairs: list[tuple[str, str]]) -> str:
        secret_key = hmac.new(
            b'WebAppData',
            self.bot_token.encode('utf-8'),
            hashlib.sha256,
        ).digest()
        data_check_string = '\n'.join(
            f'{key}={value}'
            for key, value in sorted((item for item in pairs if item[0] != 'hash'), key=lambda item: item[0])
        )
        signature = hmac.new(
            secret_key,
            data_check_string.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        encoded_pairs = [
            f'{quote(key, safe="")}={quote(value, safe="")}'
            for key, value in [*pairs, ('hash', signature)]
        ]
        return '&'.join(encoded_pairs)


if __name__ == '__main__':
    unittest.main()
