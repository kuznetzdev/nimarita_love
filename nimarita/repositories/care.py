from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from nimarita.catalog import CareTemplateSeed
from nimarita.domain.enums import CareDispatchStatus
from nimarita.domain.models import CareDispatch, CareTemplate
from nimarita.infra.sqlite import SQLiteDatabase


class CareRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def seed_templates(self, templates: Iterable[CareTemplateSeed], *, now: datetime) -> int:
        inserted = 0
        async with self._db.transaction() as tx:
            for item in templates:
                await tx.execute(
                    """
                    INSERT INTO care_templates (
                        template_code,
                        category,
                        category_label,
                        title,
                        body,
                        emoji,
                        is_active,
                        sort_order,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(template_code) DO UPDATE SET
                        category = excluded.category,
                        category_label = excluded.category_label,
                        title = excluded.title,
                        body = excluded.body,
                        emoji = excluded.emoji,
                        is_active = excluded.is_active,
                        sort_order = excluded.sort_order,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item.code,
                        item.category,
                        item.category_label,
                        item.title,
                        item.body,
                        item.emoji,
                        item.sort_order,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                inserted += 1
        return inserted

    async def list_templates(self, *, category: str | None = None, limit: int = 500) -> list[CareTemplate]:
        if category:
            rows = await self._db.fetchall(
                """
                SELECT * FROM care_templates
                WHERE is_active = 1 AND category = ?
                ORDER BY sort_order ASC, id ASC
                LIMIT ?
                """,
                (category, limit),
            )
        else:
            rows = await self._db.fetchall(
                """
                SELECT * FROM care_templates
                WHERE is_active = 1
                ORDER BY category_label ASC, sort_order ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            )
        return [_row_to_template(row) for row in rows]

    async def count_templates(self) -> int:
        row = await self._db.fetchone('SELECT COUNT(*) AS count FROM care_templates')
        return int(row['count']) if row is not None else 0

    async def get_template_by_code(self, template_code: str) -> CareTemplate | None:
        row = await self._db.fetchone(
            'SELECT * FROM care_templates WHERE template_code = ? AND is_active = 1',
            (template_code,),
        )
        return _row_to_template(row) if row is not None else None

    async def count_sent_since(self, *, sender_user_id: int, pair_id: int, since: datetime) -> int:
        row = await self._db.fetchone(
            """
            SELECT COUNT(*) AS count
            FROM care_dispatches
            WHERE sender_user_id = ?
              AND pair_id = ?
              AND created_at >= ?
              AND status IN (?, ?, ?, ?)
            """,
            (
                sender_user_id,
                pair_id,
                since.isoformat(),
                CareDispatchStatus.PENDING.value,
                CareDispatchStatus.PROCESSING.value,
                CareDispatchStatus.SENT.value,
                CareDispatchStatus.RESPONDED.value,
            ),
        )
        return int(row['count']) if row is not None else 0

    async def has_recent_duplicate(
        self,
        *,
        sender_user_id: int,
        pair_id: int,
        template_code: str,
        since: datetime,
    ) -> bool:
        row = await self._db.fetchone(
            """
            SELECT id
            FROM care_dispatches
            WHERE sender_user_id = ?
              AND pair_id = ?
              AND template_code = ?
              AND created_at >= ?
              AND status IN (?, ?, ?, ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                sender_user_id,
                pair_id,
                template_code,
                since.isoformat(),
                CareDispatchStatus.PENDING.value,
                CareDispatchStatus.PROCESSING.value,
                CareDispatchStatus.SENT.value,
                CareDispatchStatus.RESPONDED.value,
            ),
        )
        return row is not None

    async def create_dispatch(
        self,
        *,
        pair_id: int,
        sender_user_id: int,
        recipient_user_id: int,
        template: CareTemplate,
        now: datetime,
    ) -> CareDispatch:
        async with self._db.transaction() as tx:
            cursor = await tx.execute(
                """
                INSERT INTO care_dispatches (
                    pair_id,
                    sender_user_id,
                    recipient_user_id,
                    template_code,
                    category,
                    category_label,
                    title,
                    body,
                    emoji,
                    status,
                    telegram_message_id,
                    response_code,
                    response_title,
                    response_body,
                    response_emoji,
                    response_clicked_at,
                    next_attempt_at_utc,
                    processing_started_at,
                    delivery_attempts_count,
                    sent_at,
                    delivered_at,
                    last_error,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, NULL, 0, NULL, NULL, NULL, ?, ?)
                """,
                (
                    pair_id,
                    sender_user_id,
                    recipient_user_id,
                    template.template_code,
                    template.category,
                    template.category_label,
                    template.title,
                    template.body,
                    template.emoji,
                    CareDispatchStatus.PENDING.value,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            dispatch_id = int(cursor.lastrowid)
            row = await tx.fetchone('SELECT * FROM care_dispatches WHERE id = ?', (dispatch_id,))
            assert row is not None
            return _row_to_dispatch(row)

    async def claim_due_dispatches(self, *, now: datetime, limit: int) -> list[CareDispatch]:
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                """
                SELECT id FROM care_dispatches
                WHERE status = ? AND next_attempt_at_utc <= ?
                ORDER BY next_attempt_at_utc ASC, id ASC
                LIMIT ?
                """,
                (CareDispatchStatus.PENDING.value, now.isoformat(), limit),
            )
            dispatch_ids = [int(row['id']) for row in rows]
            if not dispatch_ids:
                return []
            placeholders = ','.join('?' for _ in dispatch_ids)
            await tx.execute(
                f"""
                UPDATE care_dispatches
                SET status = ?,
                    processing_started_at = ?,
                    delivery_attempts_count = delivery_attempts_count + 1,
                    updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (
                    CareDispatchStatus.PROCESSING.value,
                    now.isoformat(),
                    now.isoformat(),
                    *dispatch_ids,
                ),
            )
            claimed = await tx.fetchall(
                f"SELECT * FROM care_dispatches WHERE id IN ({placeholders}) ORDER BY id ASC",
                tuple(dispatch_ids),
            )
            return [_row_to_dispatch(row) for row in claimed]

    async def mark_sent(self, *, dispatch_id: int, telegram_message_id: int, now: datetime) -> CareDispatch:
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                UPDATE care_dispatches
                SET status = ?,
                    telegram_message_id = ?,
                    sent_at = ?,
                    delivered_at = ?,
                    updated_at = ?,
                    processing_started_at = NULL,
                    last_error = NULL
                WHERE id = ?
                """,
                (
                    CareDispatchStatus.SENT.value,
                    telegram_message_id,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    dispatch_id,
                ),
            )
            row = await tx.fetchone('SELECT * FROM care_dispatches WHERE id = ?', (dispatch_id,))
            assert row is not None
            return _row_to_dispatch(row)

    async def mark_failed(
        self,
        *,
        dispatch_id: int,
        error_text: str,
        final_failure: bool,
        next_attempt_at_utc: datetime | None,
        now: datetime,
    ) -> CareDispatch:
        status = CareDispatchStatus.FAILED.value if final_failure else CareDispatchStatus.PENDING.value
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                UPDATE care_dispatches
                SET status = ?,
                    next_attempt_at_utc = ?,
                    processing_started_at = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    (next_attempt_at_utc or now).isoformat(),
                    error_text[:500],
                    now.isoformat(),
                    dispatch_id,
                ),
            )
            row = await tx.fetchone('SELECT * FROM care_dispatches WHERE id = ?', (dispatch_id,))
            assert row is not None
            return _row_to_dispatch(row)

    async def requeue_stale_processing(
        self,
        *,
        now: datetime,
        stale_before: datetime,
        max_retries: int,
        retry_base_seconds: int,
    ) -> int:
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                """
                SELECT * FROM care_dispatches
                WHERE status = ? AND processing_started_at IS NOT NULL AND processing_started_at <= ?
                ORDER BY processing_started_at ASC, id ASC
                """,
                (CareDispatchStatus.PROCESSING.value, stale_before.isoformat()),
            )
            touched = 0
            for row in rows:
                dispatch = _row_to_dispatch(row)
                final_failure = dispatch.delivery_attempts_count >= max_retries
                next_attempt = None
                status = CareDispatchStatus.FAILED.value if final_failure else CareDispatchStatus.PENDING.value
                if not final_failure:
                    backoff_seconds = retry_base_seconds * (2 ** max(dispatch.delivery_attempts_count - 1, 0))
                    next_attempt = now + timedelta(seconds=backoff_seconds)
                await tx.execute(
                    """
                    UPDATE care_dispatches
                    SET status = ?,
                        next_attempt_at_utc = ?,
                        processing_started_at = NULL,
                        last_error = COALESCE(last_error, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        (next_attempt or now).isoformat(),
                        'Восстановлено после зависшего состояния обработки.',
                        now.isoformat(),
                        dispatch.id,
                    ),
                )
                touched += 1
            return touched

    async def cancel_open_for_pair(self, *, pair_id: int, now: datetime, reason: str = 'Пара завершена до доставки.') -> int:
        async with self._db.transaction() as tx:
            cursor = await tx.execute(
                """
                UPDATE care_dispatches
                SET status = ?,
                    next_attempt_at_utc = ?,
                    processing_started_at = NULL,
                    last_error = COALESCE(last_error, ?),
                    updated_at = ?
                WHERE pair_id = ? AND status IN (?, ?)
                """,
                (
                    CareDispatchStatus.FAILED.value,
                    now.isoformat(),
                    reason,
                    now.isoformat(),
                    pair_id,
                    CareDispatchStatus.PENDING.value,
                    CareDispatchStatus.PROCESSING.value,
                ),
            )
            return int(cursor.rowcount)

    async def register_response(
        self,
        *,
        dispatch_id: int,
        recipient_user_id: int,
        response_code: str,
        response_title: str,
        response_body: str,
        response_emoji: str,
        now: datetime,
    ) -> CareDispatch:
        async with self._db.transaction() as tx:
            row = await tx.fetchone('SELECT * FROM care_dispatches WHERE id = ?', (dispatch_id,))
            if row is None:
                raise LookupError('Сообщение заботы не найдено.')
            dispatch = _row_to_dispatch(row)
            if dispatch.recipient_user_id != recipient_user_id:
                raise PermissionError('Только получатель может ответить на это сообщение заботы.')
            if dispatch.status in {CareDispatchStatus.FAILED, CareDispatchStatus.PENDING, CareDispatchStatus.PROCESSING}:
                raise ValueError('Сообщение заботы пока не готово к быстрому ответу.')
            if dispatch.response_code is not None:
                raise ValueError('Для этого сообщения заботы быстрый ответ уже выбран.')
            await tx.execute(
                """
                UPDATE care_dispatches
                SET status = ?,
                    response_code = ?,
                    response_title = ?,
                    response_body = ?,
                    response_emoji = ?,
                    response_clicked_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    CareDispatchStatus.RESPONDED.value,
                    response_code,
                    response_title,
                    response_body,
                    response_emoji,
                    now.isoformat(),
                    now.isoformat(),
                    dispatch_id,
                ),
            )
            updated = await tx.fetchone('SELECT * FROM care_dispatches WHERE id = ?', (dispatch_id,))
            assert updated is not None
            return _row_to_dispatch(updated)

    async def get_dispatch(self, dispatch_id: int) -> CareDispatch | None:
        row = await self._db.fetchone('SELECT * FROM care_dispatches WHERE id = ?', (dispatch_id,))
        return _row_to_dispatch(row) if row is not None else None

    async def list_history_for_pair(self, *, pair_id: int, limit: int = 50) -> list[CareDispatch]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM care_dispatches
            WHERE pair_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (pair_id, limit),
        )
        return [_row_to_dispatch(row) for row in rows]



def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)



def _row_to_template(row: Any) -> CareTemplate:
    return CareTemplate(
        id=int(row['id']),
        template_code=row['template_code'],
        category=row['category'],
        category_label=row['category_label'],
        title=row['title'],
        body=row['body'],
        emoji=row['emoji'],
        is_active=bool(row['is_active']),
        sort_order=int(row['sort_order']),
        created_at=datetime.fromisoformat(row['created_at']),
        updated_at=datetime.fromisoformat(row['updated_at']),
    )



def _row_to_dispatch(row: Any) -> CareDispatch:
    return CareDispatch(
        id=int(row['id']),
        pair_id=int(row['pair_id']),
        sender_user_id=int(row['sender_user_id']),
        recipient_user_id=int(row['recipient_user_id']),
        template_code=row['template_code'],
        category=row['category'],
        category_label=row['category_label'],
        title=row['title'],
        body=row['body'],
        emoji=row['emoji'],
        status=CareDispatchStatus(row['status']),
        telegram_message_id=int(row['telegram_message_id']) if row['telegram_message_id'] is not None else None,
        response_code=row['response_code'],
        response_title=row['response_title'],
        response_body=row['response_body'],
        response_emoji=row['response_emoji'],
        response_clicked_at=_parse_datetime(row['response_clicked_at']),
        next_attempt_at_utc=_parse_datetime(row['next_attempt_at_utc']) if 'next_attempt_at_utc' in row.keys() else None,
        processing_started_at=_parse_datetime(row['processing_started_at']) if 'processing_started_at' in row.keys() else None,
        delivery_attempts_count=int(row['delivery_attempts_count']) if 'delivery_attempts_count' in row.keys() and row['delivery_attempts_count'] is not None else 0,
        sent_at=_parse_datetime(row['sent_at']),
        delivered_at=_parse_datetime(row['delivered_at']),
        last_error=row['last_error'],
        created_at=datetime.fromisoformat(row['created_at']),
        updated_at=datetime.fromisoformat(row['updated_at']),
    )
