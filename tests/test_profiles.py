from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from bot.profiles import UserProfileRegistry


class UserProfileRegistryTests(TestCase):
    def test_resolves_by_label_and_role(self) -> None:
        with TemporaryDirectory() as temp_dir:
            profiles_path = Path(temp_dir) / "profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "profiles": [
                            {
                                "id": 1,
                                "label": "Nick",
                                "role": "boyfriend",
                                "gender": "male",
                            },
                            {
                                "id": 2,
                                "label": "Margarette",
                                "role": "girlfriend",
                                "gender": "female",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            registry = UserProfileRegistry.from_file(profiles_path)

            self.assertTrue(registry.is_allowed_user(1))
            self.assertEqual(registry.resolve("Nick").id, 1)
            self.assertEqual(registry.resolve("girlfriend").id, 2)
