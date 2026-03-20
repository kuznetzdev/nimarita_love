from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nimarita.domain.enums import RelationshipRole
from nimarita.domain.models import TelegramUserSnapshot, User
from nimarita.infra.sqlite import SQLiteDatabase


class UserRepository:
    def __init__(self, db: SQLiteDatabase, default_timezone: str) -> None:
        self._db = db
        self._default_timezone = default_timezone

    async def upsert_telegram_user(
        self,
        snapshot: TelegramUserSnapshot,
        *,
        started_bot: bool,
    ) -> User:
        now = datetime.now(tz=UTC)
        row = await self._db.fetchone(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (snapshot.telegram_user_id,),
        )
        if row is None:
            await self._db.execute(
                """
                INSERT INTO users (
                    telegram_user_id,
                    private_chat_id,
                    username,
                    first_name,
                    last_name,
                    language_code,
                    timezone,
                    relationship_role,
                    started_bot,
                    created_at,
                    updated_at,
                    last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.telegram_user_id,
                    snapshot.chat_id if started_bot else None,
                    snapshot.username,
                    snapshot.first_name,
                    snapshot.last_name,
                    snapshot.language_code,
                    self._default_timezone,
                    RelationshipRole.UNSPECIFIED.value,
                    1 if started_bot else 0,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        else:
            await self._db.execute(
                """
                UPDATE users
                SET private_chat_id = ?,
                    username = ?,
                    first_name = ?,
                    last_name = ?,
                    language_code = ?,
                    started_bot = CASE WHEN ? = 1 THEN 1 ELSE started_bot END,
                    updated_at = ?,
                    last_seen_at = ?
                WHERE telegram_user_id = ?
                """,
                (
                    snapshot.chat_id if started_bot else row["private_chat_id"],
                    snapshot.username,
                    snapshot.first_name,
                    snapshot.last_name,
                    snapshot.language_code,
                    1 if started_bot else 0,
                    now.isoformat(),
                    now.isoformat(),
                    snapshot.telegram_user_id,
                ),
            )
        fresh = await self.get_by_telegram_user_id(snapshot.telegram_user_id)
        assert fresh is not None
        return fresh

    async def set_timezone(self, user_id: int, timezone: str) -> None:
        now = datetime.now(tz=UTC)
        await self._db.execute(
            "UPDATE users SET timezone = ?, updated_at = ? WHERE id = ?",
            (timezone, now.isoformat(), user_id),
        )

    async def set_relationship_role(self, user_id: int, role: RelationshipRole) -> None:
        now = datetime.now(tz=UTC)
        await self._db.execute(
            'UPDATE users SET relationship_role = ?, updated_at = ? WHERE id = ?',
            (role.value, now.isoformat(), user_id),
        )

    async def list_private_chat_users(self, *, started_only: bool = True) -> list[User]:
        query = (
            "SELECT * FROM users WHERE private_chat_id IS NOT NULL AND started_bot = 1 ORDER BY id ASC"
            if started_only
            else "SELECT * FROM users WHERE private_chat_id IS NOT NULL ORDER BY id ASC"
        )
        rows = await self._db.fetchall(query)
        return [_row_to_user(row) for row in rows]

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> User | None:
        row = await self._db.fetchone(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        return _row_to_user(row) if row is not None else None

    async def get_by_id(self, user_id: int) -> User | None:
        row = await self._db.fetchone(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        )
        return _row_to_user(row) if row is not None else None



def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)



def _row_to_user(row: Any) -> User:
    return User(
        id=int(row["id"]),
        telegram_user_id=int(row["telegram_user_id"]),
        private_chat_id=int(row["private_chat_id"]) if row["private_chat_id"] is not None else None,
        username=row["username"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        language_code=row["language_code"],
        timezone=row["timezone"],
        relationship_role=RelationshipRole(row['relationship_role']) if row['relationship_role'] else RelationshipRole.UNSPECIFIED,
        started_bot=bool(row["started_bot"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_seen_at=_parse_datetime(row["last_seen_at"]),
    )
