from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from nimarita.domain.enums import EphemeralMessageStatus
from nimarita.domain.models import EphemeralMessage, UIPanel
from nimarita.infra.sqlite import SQLiteDatabase


class UIPanelRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def get_panel(self, *, user_id: int, panel_key: str) -> UIPanel | None:
        row = await self._db.fetchone(
            "SELECT * FROM ui_panels WHERE user_id = ? AND panel_key = ?",
            (user_id, panel_key),
        )
        return _row_to_panel(row) if row is not None else None

    async def upsert_panel(
        self,
        *,
        user_id: int,
        panel_key: str,
        chat_id: int,
        message_id: int,
        now: datetime,
    ) -> UIPanel:
        await self._db.execute(
            """
            INSERT INTO ui_panels (user_id, panel_key, chat_id, message_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, panel_key) DO UPDATE SET
                chat_id = excluded.chat_id,
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (user_id, panel_key, chat_id, message_id, now.isoformat(), now.isoformat()),
        )
        row = await self._db.fetchone(
            "SELECT * FROM ui_panels WHERE user_id = ? AND panel_key = ?",
            (user_id, panel_key),
        )
        assert row is not None
        return _row_to_panel(row)

    async def delete_panel(self, *, user_id: int, panel_key: str) -> None:
        await self._db.execute(
            "DELETE FROM ui_panels WHERE user_id = ? AND panel_key = ?",
            (user_id, panel_key),
        )


class EphemeralMessageRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def schedule_delete(
        self,
        *,
        chat_id: int,
        message_id: int,
        kind: str,
        delete_after_utc: datetime,
        now: datetime,
    ) -> EphemeralMessage:
        await self._db.execute(
            """
            INSERT INTO ephemeral_messages (
                chat_id,
                message_id,
                kind,
                delete_after_utc,
                status,
                attempts_count,
                last_error,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                kind = excluded.kind,
                delete_after_utc = excluded.delete_after_utc,
                status = excluded.status,
                updated_at = excluded.updated_at,
                last_error = NULL
            """,
            (
                chat_id,
                message_id,
                kind,
                delete_after_utc.isoformat(),
                EphemeralMessageStatus.PENDING.value,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        row = await self._db.fetchone(
            "SELECT * FROM ephemeral_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        assert row is not None
        return _row_to_ephemeral(row)

    async def claim_due(self, *, now: datetime, limit: int) -> list[EphemeralMessage]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM ephemeral_messages
            WHERE status = ? AND delete_after_utc <= ?
            ORDER BY delete_after_utc ASC, id ASC
            LIMIT ?
            """,
            (EphemeralMessageStatus.PENDING.value, now.isoformat(), limit),
        )
        return [_row_to_ephemeral(row) for row in rows]

    async def mark_deleted(self, *, item_id: int, now: datetime) -> None:
        await self._db.execute(
            "UPDATE ephemeral_messages SET status = ?, updated_at = ? WHERE id = ?",
            (EphemeralMessageStatus.DELETED.value, now.isoformat(), item_id),
        )

    async def mark_failed(self, *, item_id: int, error_text: str, retry_after_seconds: int | None, now: datetime) -> None:
        if retry_after_seconds and retry_after_seconds > 0:
            next_due = now + timedelta(seconds=retry_after_seconds)
            await self._db.execute(
                """
                UPDATE ephemeral_messages
                SET status = ?,
                    attempts_count = attempts_count + 1,
                    delete_after_utc = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    EphemeralMessageStatus.PENDING.value,
                    next_due.isoformat(),
                    error_text[:500],
                    now.isoformat(),
                    item_id,
                ),
            )
            return
        await self._db.execute(
            """
            UPDATE ephemeral_messages
            SET status = ?, attempts_count = attempts_count + 1, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                EphemeralMessageStatus.FAILED.value,
                error_text[:500],
                now.isoformat(),
                item_id,
            ),
        )



def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)



def _row_to_panel(row: Any) -> UIPanel:
    return UIPanel(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        panel_key=row["panel_key"],
        chat_id=int(row["chat_id"]),
        message_id=int(row["message_id"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )



def _row_to_ephemeral(row: Any) -> EphemeralMessage:
    return EphemeralMessage(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        message_id=int(row["message_id"]),
        kind=row["kind"],
        delete_after_utc=datetime.fromisoformat(row["delete_after_utc"]),
        status=EphemeralMessageStatus(row["status"]),
        attempts_count=int(row["attempts_count"]),
        last_error=row["last_error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
