from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from nimarita.domain.models import AuditLog
from nimarita.infra.sqlite import SQLiteDatabase


class AuditRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def append(
        self,
        *,
        actor_user_id: int | None,
        entity_type: str,
        entity_id: str | None,
        action: str,
        payload: dict[str, Any] | None,
        request_id: str | None,
        now: datetime,
    ) -> AuditLog:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else None
        async with self._db.transaction() as tx:
            cursor = await tx.execute(
                """
                INSERT INTO audit_logs (
                    actor_user_id,
                    entity_type,
                    entity_id,
                    action,
                    payload_json,
                    request_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_user_id,
                    entity_type,
                    entity_id,
                    action,
                    payload_json,
                    request_id,
                    now.isoformat(),
                ),
            )
            row = await tx.fetchone('SELECT * FROM audit_logs WHERE id = ?', (int(cursor.lastrowid),))
            assert row is not None
            return _row_to_audit_log(row)

    async def recent(self, *, limit: int = 100) -> list[AuditLog]:
        rows = await self._db.fetchall(
            'SELECT * FROM audit_logs ORDER BY created_at DESC, id DESC LIMIT ?',
            (limit,),
        )
        return [_row_to_audit_log(row) for row in rows]



def _row_to_audit_log(row: Any) -> AuditLog:
    return AuditLog(
        id=int(row['id']),
        actor_user_id=int(row['actor_user_id']) if row['actor_user_id'] is not None else None,
        entity_type=row['entity_type'],
        entity_id=row['entity_id'],
        action=row['action'],
        payload_json=row['payload_json'],
        request_id=row['request_id'],
        created_at=datetime.fromisoformat(row['created_at']),
    )
