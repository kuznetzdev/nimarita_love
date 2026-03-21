from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from nimarita.domain.enums import ReminderIntervalUnit, ReminderOccurrenceStatus, ReminderRuleKind, ReminderRuleStatus
from nimarita.domain.models import ReminderOccurrence, ReminderRule
from nimarita.infra.sqlite import SQLiteDatabase, SQLiteTransaction


class ReminderRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def create_reminder(
        self,
        *,
        pair_id: int,
        creator_user_id: int,
        recipient_user_id: int,
        kind: ReminderRuleKind,
        text: str,
        creator_timezone: str,
        scheduled_at_utc: datetime,
        now: datetime,
        recurrence_every: int = 1,
        recurrence_unit: ReminderIntervalUnit | None = None,
    ) -> tuple[ReminderRule, ReminderOccurrence]:
        async with self._db.transaction() as tx:
            cursor = await tx.execute(
                """
                INSERT INTO reminder_rules (
                    pair_id,
                    creator_user_id,
                    recipient_user_id,
                    kind,
                    text,
                    creator_timezone,
                    origin_scheduled_at_utc,
                    recurrence_every,
                    recurrence_unit,
                    status,
                    cancelled_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    pair_id,
                    creator_user_id,
                    recipient_user_id,
                    kind.value,
                    text,
                    creator_timezone,
                    scheduled_at_utc.isoformat(),
                    recurrence_every,
                    recurrence_unit.value if recurrence_unit is not None else None,
                    ReminderRuleStatus.ACTIVE.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            rule_id = int(cursor.lastrowid)
            await tx.execute(
                """
                INSERT INTO reminder_occurrences (
                    rule_id,
                    pair_id,
                    creator_user_id,
                    recipient_user_id,
                    text,
                    scheduled_at_utc,
                    next_attempt_at_utc,
                    status,
                    handled_action,
                    telegram_message_id,
                    delivery_attempts_count,
                    last_error,
                    sent_at,
                    delivered_at,
                    acknowledged_at,
                    cancelled_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    rule_id,
                    pair_id,
                    creator_user_id,
                    recipient_user_id,
                    text,
                    scheduled_at_utc.isoformat(),
                    scheduled_at_utc.isoformat(),
                    ReminderOccurrenceStatus.SCHEDULED.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            rule_row = await tx.fetchone("SELECT * FROM reminder_rules WHERE id = ?", (rule_id,))
            occurrence_row = await tx.fetchone(
                "SELECT * FROM reminder_occurrences WHERE rule_id = ? ORDER BY id DESC LIMIT 1",
                (rule_id,),
            )
            assert rule_row is not None and occurrence_row is not None
            return _row_to_rule(rule_row), _row_to_occurrence(occurrence_row)

    async def _create_occurrence_tx(
        self,
        tx: SQLiteTransaction,
        *,
        rule: ReminderRule,
        scheduled_at_utc: datetime,
        now: datetime,
    ) -> ReminderOccurrence:
        cursor = await tx.execute(
            """
            INSERT INTO reminder_occurrences (
                rule_id,
                pair_id,
                creator_user_id,
                recipient_user_id,
                text,
                scheduled_at_utc,
                next_attempt_at_utc,
                status,
                handled_action,
                telegram_message_id,
                delivery_attempts_count,
                last_error,
                sent_at,
                delivered_at,
                acknowledged_at,
                cancelled_at,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                rule.id,
                rule.pair_id,
                rule.creator_user_id,
                rule.recipient_user_id,
                rule.text,
                scheduled_at_utc.isoformat(),
                scheduled_at_utc.isoformat(),
                ReminderOccurrenceStatus.SCHEDULED.value,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        occurrence_id = int(cursor.lastrowid)
        row = await tx.fetchone('SELECT * FROM reminder_occurrences WHERE id = ?', (occurrence_id,))
        assert row is not None
        return _row_to_occurrence(row)

    async def create_one_time_reminder(
        self,
        *,
        pair_id: int,
        creator_user_id: int,
        recipient_user_id: int,
        text: str,
        creator_timezone: str,
        scheduled_at_utc: datetime,
        now: datetime,
    ) -> tuple[ReminderRule, ReminderOccurrence]:
        return await self.create_reminder(
            pair_id=pair_id,
            creator_user_id=creator_user_id,
            recipient_user_id=recipient_user_id,
            kind=ReminderRuleKind.ONE_TIME,
            text=text,
            creator_timezone=creator_timezone,
            scheduled_at_utc=scheduled_at_utc,
            now=now,
            recurrence_every=1,
            recurrence_unit=None,
        )

    async def create_occurrence(
        self,
        *,
        rule: ReminderRule,
        scheduled_at_utc: datetime,
        now: datetime,
    ) -> ReminderOccurrence:
        async with self._db.transaction() as tx:
            occurrence = await self._create_occurrence_tx(
                tx,
                rule=rule,
                scheduled_at_utc=scheduled_at_utc,
                now=now,
            )
            return occurrence

    async def occurrence_exists(self, *, rule_id: int, scheduled_at_utc: datetime) -> bool:
        row = await self._db.fetchone(
            'SELECT id FROM reminder_occurrences WHERE rule_id = ? AND scheduled_at_utc = ? LIMIT 1',
            (rule_id, scheduled_at_utc.isoformat()),
        )
        return row is not None

    async def list_for_pair(self, pair_id: int, *, limit: int = 30) -> list[tuple[ReminderRule, ReminderOccurrence]]:
        rows = await self._db.fetchall(
            """
            SELECT
                rr.id AS rule_id,
                rr.pair_id AS rule_pair_id,
                rr.creator_user_id AS rule_creator_user_id,
                rr.recipient_user_id AS rule_recipient_user_id,
                rr.kind AS rule_kind,
                rr.text AS rule_text,
                rr.creator_timezone AS rule_creator_timezone,
                rr.origin_scheduled_at_utc AS rule_origin_scheduled_at_utc,
                rr.recurrence_every AS rule_recurrence_every,
                rr.recurrence_unit AS rule_recurrence_unit,
                rr.status AS rule_status,
                rr.cancelled_at AS rule_cancelled_at,
                rr.created_at AS rule_created_at,
                rr.updated_at AS rule_updated_at,
                ro.id AS occurrence_id,
                ro.rule_id AS occurrence_rule_id,
                ro.pair_id AS occurrence_pair_id,
                ro.creator_user_id AS occurrence_creator_user_id,
                ro.recipient_user_id AS occurrence_recipient_user_id,
                ro.text AS occurrence_text,
                ro.scheduled_at_utc AS occurrence_scheduled_at_utc,
                ro.next_attempt_at_utc AS occurrence_next_attempt_at_utc,
                ro.status AS occurrence_status,
                ro.handled_action AS occurrence_handled_action,
                ro.telegram_message_id AS occurrence_telegram_message_id,
                ro.delivery_attempts_count AS occurrence_delivery_attempts_count,
                ro.last_error AS occurrence_last_error,
                ro.sent_at AS occurrence_sent_at,
                ro.delivered_at AS occurrence_delivered_at,
                ro.acknowledged_at AS occurrence_acknowledged_at,
                ro.cancelled_at AS occurrence_cancelled_at,
                ro.created_at AS occurrence_created_at,
                ro.updated_at AS occurrence_updated_at
            FROM reminder_occurrences ro
            JOIN reminder_rules rr ON rr.id = ro.rule_id
            WHERE ro.pair_id = ?
            ORDER BY ro.scheduled_at_utc DESC, ro.id DESC
            LIMIT ?
            """,
            (pair_id, limit),
        )
        return [(_joined_row_to_rule(row), _joined_row_to_occurrence(row)) for row in rows]

    async def cancel_rule(self, *, pair_id: int, rule_id: int, actor_user_id: int, now: datetime) -> tuple[ReminderRule, ReminderOccurrence]:
        async with self._db.transaction() as tx:
            rule_row = await tx.fetchone(
                "SELECT * FROM reminder_rules WHERE id = ? AND pair_id = ?",
                (rule_id, pair_id),
            )
            if rule_row is None:
                raise LookupError("Правило напоминания не найдено.")
            rule = _row_to_rule(rule_row)
            if rule.creator_user_id != actor_user_id:
                raise PermissionError("Только создатель может отменить это напоминание.")
            occurrence_row = await tx.fetchone(
                "SELECT * FROM reminder_occurrences WHERE rule_id = ? ORDER BY id DESC LIMIT 1",
                (rule_id,),
            )
            if occurrence_row is None:
                raise LookupError("Экземпляр напоминания не найден.")
            occurrence = _row_to_occurrence(occurrence_row)
            if occurrence.status is not ReminderOccurrenceStatus.SCHEDULED:
                raise ValueError("Можно отменить только запланированные напоминания.")
            await tx.execute(
                """
                UPDATE reminder_rules
                SET status = ?, cancelled_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ReminderRuleStatus.CANCELLED.value,
                    now.isoformat(),
                    now.isoformat(),
                    rule_id,
                ),
            )
            await tx.execute(
                """
                UPDATE reminder_occurrences
                SET status = ?, cancelled_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ReminderOccurrenceStatus.CANCELLED.value,
                    now.isoformat(),
                    now.isoformat(),
                    occurrence.id,
                ),
            )
            updated_rule_row = await tx.fetchone("SELECT * FROM reminder_rules WHERE id = ?", (rule_id,))
            updated_occurrence_row = await tx.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence.id,))
            assert updated_rule_row is not None and updated_occurrence_row is not None
            return _row_to_rule(updated_rule_row), _row_to_occurrence(updated_occurrence_row)

    async def update_rule(
        self,
        *,
        pair_id: int,
        rule_id: int,
        actor_user_id: int,
        text: str,
        kind: ReminderRuleKind,
        creator_timezone: str,
        scheduled_at_utc: datetime,
        recurrence_every: int,
        recurrence_unit: ReminderIntervalUnit | None,
        now: datetime,
    ) -> tuple[ReminderRule, ReminderOccurrence]:
        async with self._db.transaction() as tx:
            rule_row = await tx.fetchone(
                "SELECT * FROM reminder_rules WHERE id = ? AND pair_id = ?",
                (rule_id, pair_id),
            )
            if rule_row is None:
                raise LookupError("Правило напоминания не найдено.")
            rule = _row_to_rule(rule_row)
            if rule.creator_user_id != actor_user_id:
                raise PermissionError("Только создатель может редактировать это напоминание.")
            if rule.status is not ReminderRuleStatus.ACTIVE:
                raise ValueError("Можно редактировать только активные напоминания.")
            occurrence_row = await tx.fetchone(
                """
                SELECT * FROM reminder_occurrences
                WHERE rule_id = ? AND status = ?
                ORDER BY scheduled_at_utc ASC, id ASC
                LIMIT 1
                """,
                (rule_id, ReminderOccurrenceStatus.SCHEDULED.value),
            )
            if occurrence_row is None:
                raise ValueError("Нет ближайшего запланированного экземпляра для редактирования.")
            occurrence = _row_to_occurrence(occurrence_row)
            await tx.execute(
                """
                UPDATE reminder_rules
                SET kind = ?,
                    text = ?,
                    creator_timezone = ?,
                    origin_scheduled_at_utc = ?,
                    recurrence_every = ?,
                    recurrence_unit = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    kind.value,
                    text,
                    creator_timezone,
                    scheduled_at_utc.isoformat(),
                    recurrence_every,
                    recurrence_unit.value if recurrence_unit is not None else None,
                    now.isoformat(),
                    rule_id,
                ),
            )
            await tx.execute(
                """
                UPDATE reminder_occurrences
                SET text = ?,
                    scheduled_at_utc = ?,
                    next_attempt_at_utc = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    text,
                    scheduled_at_utc.isoformat(),
                    scheduled_at_utc.isoformat(),
                    now.isoformat(),
                    occurrence.id,
                ),
            )
            updated_rule_row = await tx.fetchone("SELECT * FROM reminder_rules WHERE id = ?", (rule_id,))
            updated_occurrence_row = await tx.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence.id,))
            assert updated_rule_row is not None and updated_occurrence_row is not None
            return _row_to_rule(updated_rule_row), _row_to_occurrence(updated_occurrence_row)

    async def cancel_open_for_pair(self, *, pair_id: int, now: datetime) -> int:
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                UPDATE reminder_rules
                SET status = ?, cancelled_at = COALESCE(cancelled_at, ?), updated_at = ?
                WHERE pair_id = ? AND status = ?
                """,
                (
                    ReminderRuleStatus.CANCELLED.value,
                    now.isoformat(),
                    now.isoformat(),
                    pair_id,
                    ReminderRuleStatus.ACTIVE.value,
                ),
            )
            cursor = await tx.execute(
                """
                UPDATE reminder_occurrences
                SET status = ?, cancelled_at = COALESCE(cancelled_at, ?), updated_at = ?
                WHERE pair_id = ? AND status IN (?, ?)
                """,
                (
                    ReminderOccurrenceStatus.CANCELLED.value,
                    now.isoformat(),
                    now.isoformat(),
                    pair_id,
                    ReminderOccurrenceStatus.SCHEDULED.value,
                    ReminderOccurrenceStatus.PROCESSING.value,
                ),
            )
            return int(cursor.rowcount)

    async def claim_due_occurrences(self, *, now: datetime, limit: int) -> list[tuple[ReminderRule, ReminderOccurrence]]:
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                """
                SELECT id FROM reminder_occurrences
                WHERE status = ? AND next_attempt_at_utc <= ?
                ORDER BY next_attempt_at_utc ASC, id ASC
                LIMIT ?
                """,
                (ReminderOccurrenceStatus.SCHEDULED.value, now.isoformat(), limit),
            )
            occurrence_ids = [int(row["id"]) for row in rows]
            if not occurrence_ids:
                return []
            placeholders = ",".join("?" for _ in occurrence_ids)
            await tx.execute(
                f"""
                UPDATE reminder_occurrences
                SET status = ?,
                    delivery_attempts_count = delivery_attempts_count + 1,
                    sent_at = ?,
                    updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (
                    ReminderOccurrenceStatus.PROCESSING.value,
                    now.isoformat(),
                    now.isoformat(),
                    *occurrence_ids,
                ),
            )
            claimed_rows = await tx.fetchall(
                f"""
                SELECT
                    rr.id AS rule_id,
                    rr.pair_id AS rule_pair_id,
                    rr.creator_user_id AS rule_creator_user_id,
                    rr.recipient_user_id AS rule_recipient_user_id,
                    rr.kind AS rule_kind,
                    rr.text AS rule_text,
                    rr.creator_timezone AS rule_creator_timezone,
                    rr.origin_scheduled_at_utc AS rule_origin_scheduled_at_utc,
                    rr.recurrence_every AS rule_recurrence_every,
                    rr.recurrence_unit AS rule_recurrence_unit,
                    rr.status AS rule_status,
                    rr.cancelled_at AS rule_cancelled_at,
                    rr.created_at AS rule_created_at,
                    rr.updated_at AS rule_updated_at,
                    ro.id AS occurrence_id,
                    ro.rule_id AS occurrence_rule_id,
                    ro.pair_id AS occurrence_pair_id,
                    ro.creator_user_id AS occurrence_creator_user_id,
                    ro.recipient_user_id AS occurrence_recipient_user_id,
                    ro.text AS occurrence_text,
                    ro.scheduled_at_utc AS occurrence_scheduled_at_utc,
                    ro.next_attempt_at_utc AS occurrence_next_attempt_at_utc,
                    ro.status AS occurrence_status,
                    ro.handled_action AS occurrence_handled_action,
                    ro.telegram_message_id AS occurrence_telegram_message_id,
                    ro.delivery_attempts_count AS occurrence_delivery_attempts_count,
                    ro.last_error AS occurrence_last_error,
                    ro.sent_at AS occurrence_sent_at,
                    ro.delivered_at AS occurrence_delivered_at,
                    ro.acknowledged_at AS occurrence_acknowledged_at,
                    ro.cancelled_at AS occurrence_cancelled_at,
                    ro.created_at AS occurrence_created_at,
                    ro.updated_at AS occurrence_updated_at
                FROM reminder_occurrences ro
                JOIN reminder_rules rr ON rr.id = ro.rule_id
                WHERE ro.id IN ({placeholders})
                ORDER BY ro.next_attempt_at_utc ASC, ro.id ASC
                """,
                tuple(occurrence_ids),
            )
            return [(_joined_row_to_rule(row), _joined_row_to_occurrence(row)) for row in claimed_rows]

    async def mark_delivered(self, *, occurrence_id: int, telegram_message_id: int, now: datetime) -> ReminderOccurrence:
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                UPDATE reminder_occurrences
                SET status = ?, telegram_message_id = ?, delivered_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ReminderOccurrenceStatus.DELIVERED.value,
                    telegram_message_id,
                    now.isoformat(),
                    now.isoformat(),
                    occurrence_id,
                ),
            )
            row = await tx.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence_id,))
            assert row is not None
            return _row_to_occurrence(row)

    async def mark_delivery_failure(
        self,
        *,
        occurrence_id: int,
        error_text: str,
        final_failure: bool,
        next_attempt_at_utc: datetime | None,
        now: datetime,
    ) -> ReminderOccurrence:
        async with self._db.transaction() as tx:
            status = ReminderOccurrenceStatus.FAILED.value if final_failure else ReminderOccurrenceStatus.SCHEDULED.value
            await tx.execute(
                """
                UPDATE reminder_occurrences
                SET status = ?,
                    last_error = ?,
                    next_attempt_at_utc = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    error_text,
                    (next_attempt_at_utc or now).isoformat(),
                    now.isoformat(),
                    occurrence_id,
                ),
            )
            row = await tx.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence_id,))
            assert row is not None
            return _row_to_occurrence(row)


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
                SELECT * FROM reminder_occurrences
                WHERE status = ? AND sent_at IS NOT NULL AND sent_at <= ?
                ORDER BY sent_at ASC, id ASC
                """
                ,
                (ReminderOccurrenceStatus.PROCESSING.value, stale_before.isoformat()),
            )
            touched = 0
            for row in rows:
                occurrence = _row_to_occurrence(row)
                final_failure = occurrence.delivery_attempts_count >= max_retries
                next_attempt_at = None
                status = ReminderOccurrenceStatus.FAILED.value if final_failure else ReminderOccurrenceStatus.SCHEDULED.value
                if not final_failure:
                    backoff_seconds = retry_base_seconds * (2 ** max(occurrence.delivery_attempts_count - 1, 0))
                    next_attempt_at = now + timedelta(seconds=backoff_seconds)
                await tx.execute(
                    """
                    UPDATE reminder_occurrences
                    SET status = ?,
                        last_error = COALESCE(last_error, ?),
                        next_attempt_at_utc = ?,
                        updated_at = ?
                    WHERE id = ?
                    """
                    ,
                    (
                        status,
                        'Восстановлено после зависшего состояния обработки.',
                        (next_attempt_at or now).isoformat(),
                        now.isoformat(),
                        occurrence.id,
                    ),
                )
                touched += 1
            return touched

    async def acknowledge(self, *, occurrence_id: int, actor_user_id: int, action: str, now: datetime) -> ReminderOccurrence:
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT * FROM reminder_occurrences WHERE id = ?",
                (occurrence_id,),
            )
            if row is None:
                raise LookupError("Экземпляр напоминания не найден.")
            occurrence = _row_to_occurrence(row)
            if occurrence.recipient_user_id != actor_user_id:
                raise PermissionError("Только получатель может подтвердить это напоминание.")
            if occurrence.status is not ReminderOccurrenceStatus.DELIVERED:
                raise ValueError("Напоминание ещё не ожидает действие получателя.")
            await tx.execute(
                """
                UPDATE reminder_occurrences
                SET status = ?, handled_action = ?, acknowledged_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ReminderOccurrenceStatus.ACKNOWLEDGED.value,
                    action,
                    now.isoformat(),
                    now.isoformat(),
                    occurrence_id,
                ),
            )
            updated_row = await tx.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence_id,))
            assert updated_row is not None
            return _row_to_occurrence(updated_row)

    async def snooze(self, *, occurrence_id: int, actor_user_id: int, minutes: int, now: datetime) -> tuple[ReminderOccurrence, ReminderOccurrence]:
        async with self._db.transaction() as tx:
            occurrence_row = await tx.fetchone(
                "SELECT * FROM reminder_occurrences WHERE id = ?",
                (occurrence_id,),
            )
            if occurrence_row is None:
                raise LookupError("Экземпляр напоминания не найден.")
            occurrence = _row_to_occurrence(occurrence_row)
            if occurrence.recipient_user_id != actor_user_id:
                raise PermissionError("Только получатель может отложить это напоминание.")
            if occurrence.status is not ReminderOccurrenceStatus.DELIVERED:
                raise ValueError("Напоминание ещё не ожидает действие получателя.")
            await tx.execute(
                """
                UPDATE reminder_occurrences
                SET status = ?, handled_action = ?, acknowledged_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ReminderOccurrenceStatus.ACKNOWLEDGED.value,
                    f"snooze_{minutes}",
                    now.isoformat(),
                    now.isoformat(),
                    occurrence_id,
                ),
            )
            follow_up_time = now + timedelta(minutes=minutes)
            cursor = await tx.execute(
                """
                INSERT INTO reminder_occurrences (
                    rule_id,
                    pair_id,
                    creator_user_id,
                    recipient_user_id,
                    text,
                    scheduled_at_utc,
                    next_attempt_at_utc,
                    status,
                    handled_action,
                    telegram_message_id,
                    delivery_attempts_count,
                    last_error,
                    sent_at,
                    delivered_at,
                    acknowledged_at,
                    cancelled_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    occurrence.rule_id,
                    occurrence.pair_id,
                    occurrence.creator_user_id,
                    occurrence.recipient_user_id,
                    occurrence.text,
                    follow_up_time.isoformat(),
                    follow_up_time.isoformat(),
                    ReminderOccurrenceStatus.SCHEDULED.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            new_occurrence_id = int(cursor.lastrowid)
            updated_current = await tx.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence_id,))
            updated_new = await tx.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (new_occurrence_id,))
            assert updated_current is not None and updated_new is not None
            return _row_to_occurrence(updated_current), _row_to_occurrence(updated_new)

    async def get_rule(self, rule_id: int) -> ReminderRule | None:
        row = await self._db.fetchone("SELECT * FROM reminder_rules WHERE id = ?", (rule_id,))
        return _row_to_rule(row) if row is not None else None

    async def get_occurrence(self, occurrence_id: int) -> ReminderOccurrence | None:
        row = await self._db.fetchone("SELECT * FROM reminder_occurrences WHERE id = ?", (occurrence_id,))
        return _row_to_occurrence(row) if row is not None else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _row_to_rule(row: Any) -> ReminderRule:
    return ReminderRule(
        id=int(row["id"]),
        pair_id=int(row["pair_id"]),
        creator_user_id=int(row["creator_user_id"]),
        recipient_user_id=int(row["recipient_user_id"]),
        kind=ReminderRuleKind(row["kind"]),
        text=row["text"],
        creator_timezone=row["creator_timezone"],
        origin_scheduled_at_utc=datetime.fromisoformat(row["origin_scheduled_at_utc"]),
        recurrence_every=int(row["recurrence_every"]) if row["recurrence_every"] is not None else 1,
        recurrence_unit=ReminderIntervalUnit(row["recurrence_unit"]) if row["recurrence_unit"] else None,
        status=ReminderRuleStatus(row["status"]),
        cancelled_at=_parse_datetime(row["cancelled_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_occurrence(row: Any) -> ReminderOccurrence:
    return ReminderOccurrence(
        id=int(row["id"]),
        rule_id=int(row["rule_id"]),
        pair_id=int(row["pair_id"]),
        creator_user_id=int(row["creator_user_id"]),
        recipient_user_id=int(row["recipient_user_id"]),
        text=row["text"],
        scheduled_at_utc=datetime.fromisoformat(row["scheduled_at_utc"]),
        next_attempt_at_utc=datetime.fromisoformat(row["next_attempt_at_utc"]),
        status=ReminderOccurrenceStatus(row["status"]),
        handled_action=row["handled_action"],
        telegram_message_id=int(row["telegram_message_id"]) if row["telegram_message_id"] is not None else None,
        delivery_attempts_count=int(row["delivery_attempts_count"]),
        last_error=row["last_error"],
        sent_at=_parse_datetime(row["sent_at"]),
        delivered_at=_parse_datetime(row["delivered_at"]),
        acknowledged_at=_parse_datetime(row["acknowledged_at"]),
        cancelled_at=_parse_datetime(row["cancelled_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _joined_row_to_rule(row: Any) -> ReminderRule:
    return ReminderRule(
        id=int(row["rule_id"]),
        pair_id=int(row["rule_pair_id"]),
        creator_user_id=int(row["rule_creator_user_id"]),
        recipient_user_id=int(row["rule_recipient_user_id"]),
        kind=ReminderRuleKind(row["rule_kind"]),
        text=row["rule_text"],
        creator_timezone=row["rule_creator_timezone"],
        origin_scheduled_at_utc=datetime.fromisoformat(row["rule_origin_scheduled_at_utc"]),
        recurrence_every=int(row["rule_recurrence_every"]) if row["rule_recurrence_every"] is not None else 1,
        recurrence_unit=ReminderIntervalUnit(row["rule_recurrence_unit"]) if row["rule_recurrence_unit"] else None,
        status=ReminderRuleStatus(row["rule_status"]),
        cancelled_at=_parse_datetime(row["rule_cancelled_at"]),
        created_at=datetime.fromisoformat(row["rule_created_at"]),
        updated_at=datetime.fromisoformat(row["rule_updated_at"]),
    )


def _joined_row_to_occurrence(row: Any) -> ReminderOccurrence:
    return ReminderOccurrence(
        id=int(row["occurrence_id"]),
        rule_id=int(row["occurrence_rule_id"]),
        pair_id=int(row["occurrence_pair_id"]),
        creator_user_id=int(row["occurrence_creator_user_id"]),
        recipient_user_id=int(row["occurrence_recipient_user_id"]),
        text=row["occurrence_text"],
        scheduled_at_utc=datetime.fromisoformat(row["occurrence_scheduled_at_utc"]),
        next_attempt_at_utc=datetime.fromisoformat(row["occurrence_next_attempt_at_utc"]),
        status=ReminderOccurrenceStatus(row["occurrence_status"]),
        handled_action=row["occurrence_handled_action"],
        telegram_message_id=(
            int(row["occurrence_telegram_message_id"])
            if row["occurrence_telegram_message_id"] is not None
            else None
        ),
        delivery_attempts_count=int(row["occurrence_delivery_attempts_count"]),
        last_error=row["occurrence_last_error"],
        sent_at=_parse_datetime(row["occurrence_sent_at"]),
        delivered_at=_parse_datetime(row["occurrence_delivered_at"]),
        acknowledged_at=_parse_datetime(row["occurrence_acknowledged_at"]),
        cancelled_at=_parse_datetime(row["occurrence_cancelled_at"]),
        created_at=datetime.fromisoformat(row["occurrence_created_at"]),
        updated_at=datetime.fromisoformat(row["occurrence_updated_at"]),
    )
