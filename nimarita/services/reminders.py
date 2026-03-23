from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from calendar import monthrange
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nimarita.config import Settings
from nimarita.domain.enums import ReminderIntervalUnit, ReminderOccurrenceStatus, ReminderRuleKind, ReminderRuleStatus
from nimarita.domain.errors import ConflictError, NotFoundError, ValidationError
from nimarita.domain.models import ReminderEnvelope, ReminderOccurrence, ReminderRule, User
from nimarita.repositories.pairing import PairingRepository
from nimarita.repositories.reminders import ReminderRepository
from nimarita.repositories.users import UserRepository
from nimarita.services.audit import AuditService

_DUPLICATE_SUBMIT_WINDOW = timedelta(seconds=30)


@dataclass(slots=True, frozen=True)
class ReminderDeliveryFailure:
    occurrence: ReminderOccurrence
    creator: User
    recipient: User
    final_failure: bool


class ReminderService:
    def __init__(
        self,
        *,
        reminders: ReminderRepository,
        pairing: PairingRepository,
        users: UserRepository,
        settings: Settings,
        audit: AuditService | None = None,
    ) -> None:
        self._reminders = reminders
        self._pairing = pairing
        self._users = users
        self._settings = settings
        self._audit = audit

    async def create_one_time_reminder(
        self,
        *,
        telegram_user_id: int,
        text: str,
        scheduled_for_local: str,
        timezone: str,
    ) -> ReminderEnvelope:
        return await self.create_reminder(
            telegram_user_id=telegram_user_id,
            text=text,
            scheduled_for_local=scheduled_for_local,
            timezone=timezone,
            kind=ReminderRuleKind.ONE_TIME,
        )

    async def create_reminder(
        self,
        *,
        telegram_user_id: int,
        text: str,
        scheduled_for_local: str,
        timezone: str,
        kind: ReminderRuleKind,
        recurrence_every: int = 1,
        recurrence_unit: ReminderIntervalUnit | None = None,
    ) -> ReminderEnvelope:
        user = await self._require_user(telegram_user_id)
        pair, partner = await self._require_active_pair(user)

        clean_text = _normalize_text(text)
        if not clean_text:
            raise ValidationError("Текст напоминания не должен быть пустым.")
        if len(clean_text) > 400:
            raise ValidationError("Текст напоминания слишком длинный. Максимум 400 символов.")

        scheduled_at_utc = _parse_local_datetime_to_utc(scheduled_for_local, timezone)
        now = datetime.now(tz=UTC)
        if scheduled_at_utc <= now - timedelta(seconds=5):
            raise ValidationError("Время напоминания должно быть в будущем.")

        recurrence_every, recurrence_unit = _normalize_recurrence(kind, recurrence_every, recurrence_unit)
        existing = await self._find_recent_duplicate_reminder(
            pair_id=pair.id,
            creator=user,
            recipient=partner,
            text=clean_text,
            timezone=timezone,
            scheduled_at_utc=scheduled_at_utc,
            kind=kind,
            recurrence_every=recurrence_every,
            recurrence_unit=recurrence_unit,
            now=now,
        )
        if existing is not None:
            await self._audit_event(
                action='reminder_idempotent_reused',
                entity_id=existing.occurrence.id,
                actor_user_id=existing.creator.id,
                payload={
                    'pair_id': existing.rule.pair_id,
                    'recipient_user_id': existing.recipient.id,
                    'scheduled_at_utc': existing.occurrence.scheduled_at_utc.isoformat(),
                    'kind': existing.rule.kind.value,
                },
            )
            return existing

        await self._users.set_timezone(user.id, timezone)
        rule, occurrence = await self._reminders.create_reminder(
            pair_id=pair.id,
            creator_user_id=user.id,
            recipient_user_id=partner.id,
            kind=kind,
            text=clean_text,
            creator_timezone=timezone,
            scheduled_at_utc=scheduled_at_utc,
            now=now,
            recurrence_every=recurrence_every,
            recurrence_unit=recurrence_unit,
        )
        creator = await self._users.get_by_id(rule.creator_user_id)
        recipient = await self._users.get_by_id(rule.recipient_user_id)
        assert creator is not None and recipient is not None
        envelope = ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient)
        await self._audit_event(
            action='reminder_created',
            entity_id=envelope.occurrence.id,
            actor_user_id=envelope.creator.id,
            payload={
                'pair_id': envelope.rule.pair_id,
                'recipient_user_id': envelope.recipient.id,
                'scheduled_at_utc': envelope.occurrence.scheduled_at_utc.isoformat(),
                'kind': envelope.rule.kind.value,
                'recurrence_every': envelope.rule.recurrence_every,
                'recurrence_unit': envelope.rule.recurrence_unit.value if envelope.rule.recurrence_unit else None,
            },
        )
        return envelope

    async def list_pair_reminders(self, *, telegram_user_id: int, limit: int = 30) -> list[ReminderEnvelope]:
        user = await self._require_user(telegram_user_id)
        pair, _partner = await self._require_active_pair(user)
        items = await self._reminders.list_for_pair(pair.id, limit=limit)
        creator_ids = {rule.creator_user_id for rule, _ in items}
        recipient_ids = {rule.recipient_user_id for rule, _ in items}
        cache: dict[int, User] = {}
        for user_id in creator_ids | recipient_ids:
            db_user = await self._users.get_by_id(user_id)
            if db_user is not None:
                cache[user_id] = db_user
        result: list[ReminderEnvelope] = []
        for rule, occurrence in items:
            creator = cache.get(rule.creator_user_id)
            recipient = cache.get(rule.recipient_user_id)
            if creator is None or recipient is None:
                continue
            result.append(ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient))
        return result

    async def cancel_reminder(self, *, telegram_user_id: int, rule_id: int) -> ReminderEnvelope:
        user = await self._require_user(telegram_user_id)
        pair, _partner = await self._require_active_pair(user)
        now = datetime.now(tz=UTC)
        try:
            rule, occurrence = await self._reminders.cancel_rule(
                pair_id=pair.id,
                rule_id=rule_id,
                actor_user_id=user.id,
                now=now,
            )
        except LookupError as error:
            raise NotFoundError("Напоминание не найдено.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ConflictError(str(error)) from error
        creator = await self._users.get_by_id(rule.creator_user_id)
        recipient = await self._users.get_by_id(rule.recipient_user_id)
        assert creator is not None and recipient is not None
        envelope = ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient)
        await self._audit_event(
            action='reminder_cancelled',
            entity_id=envelope.occurrence.id,
            actor_user_id=user.id,
            payload={
                'pair_id': pair.id,
                'rule_id': envelope.rule.id,
                'recipient_user_id': envelope.recipient.id,
            },
        )
        return envelope

    async def update_reminder(
        self,
        *,
        telegram_user_id: int,
        rule_id: int,
        text: str,
        scheduled_for_local: str,
        timezone: str,
        kind: ReminderRuleKind,
        recurrence_every: int = 1,
        recurrence_unit: ReminderIntervalUnit | None = None,
    ) -> ReminderEnvelope:
        user = await self._require_user(telegram_user_id)
        pair, _partner = await self._require_active_pair(user)

        clean_text = _normalize_text(text)
        if not clean_text:
            raise ValidationError("Текст напоминания не должен быть пустым.")
        if len(clean_text) > 400:
            raise ValidationError("Текст напоминания слишком длинный. Максимум 400 символов.")

        scheduled_at_utc = _parse_local_datetime_to_utc(scheduled_for_local, timezone)
        now = datetime.now(tz=UTC)
        if scheduled_at_utc <= now - timedelta(seconds=5):
            raise ValidationError("Время напоминания должно быть в будущем.")

        recurrence_every, recurrence_unit = _normalize_recurrence(kind, recurrence_every, recurrence_unit)
        await self._users.set_timezone(user.id, timezone)
        try:
            rule, occurrence = await self._reminders.update_rule(
                pair_id=pair.id,
                rule_id=rule_id,
                actor_user_id=user.id,
                text=clean_text,
                kind=kind,
                creator_timezone=timezone,
                scheduled_at_utc=scheduled_at_utc,
                recurrence_every=recurrence_every,
                recurrence_unit=recurrence_unit,
                now=now,
            )
        except LookupError as error:
            raise NotFoundError("Напоминание не найдено.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ConflictError(str(error)) from error
        creator = await self._users.get_by_id(rule.creator_user_id)
        recipient = await self._users.get_by_id(rule.recipient_user_id)
        assert creator is not None and recipient is not None
        envelope = ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient)
        await self._audit_event(
            action='reminder_updated',
            entity_id=envelope.occurrence.id,
            actor_user_id=envelope.creator.id,
            payload={
                'rule_id': envelope.rule.id,
                'scheduled_at_utc': envelope.occurrence.scheduled_at_utc.isoformat(),
                'kind': envelope.rule.kind.value,
                'recurrence_every': envelope.rule.recurrence_every,
                'recurrence_unit': envelope.rule.recurrence_unit.value if envelope.rule.recurrence_unit else None,
            },
        )
        return envelope

    async def restore_reminder(
        self,
        *,
        telegram_user_id: int,
        rule_id: int,
        text: str,
        scheduled_for_local: str,
        timezone: str,
        kind: ReminderRuleKind,
        recurrence_every: int = 1,
        recurrence_unit: ReminderIntervalUnit | None = None,
    ) -> ReminderEnvelope:
        user = await self._require_user(telegram_user_id)
        pair, _partner = await self._require_active_pair(user)

        clean_text = _normalize_text(text)
        if not clean_text:
            raise ValidationError("Текст напоминания не должен быть пустым.")
        if len(clean_text) > 400:
            raise ValidationError("Текст напоминания слишком длинный. Максимум 400 символов.")

        scheduled_at_utc = _parse_local_datetime_to_utc(scheduled_for_local, timezone)
        now = datetime.now(tz=UTC)
        if scheduled_at_utc <= now - timedelta(seconds=5):
            raise ValidationError("Время напоминания должно быть в будущем.")

        recurrence_every, recurrence_unit = _normalize_recurrence(kind, recurrence_every, recurrence_unit)
        await self._users.set_timezone(user.id, timezone)
        try:
            rule, occurrence = await self._reminders.restore_rule(
                pair_id=pair.id,
                rule_id=rule_id,
                actor_user_id=user.id,
                text=clean_text,
                kind=kind,
                creator_timezone=timezone,
                scheduled_at_utc=scheduled_at_utc,
                recurrence_every=recurrence_every,
                recurrence_unit=recurrence_unit,
                now=now,
            )
        except LookupError as error:
            raise NotFoundError("Напоминание не найдено.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ConflictError(str(error)) from error
        creator = await self._users.get_by_id(rule.creator_user_id)
        recipient = await self._users.get_by_id(rule.recipient_user_id)
        assert creator is not None and recipient is not None
        envelope = ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient)
        await self._audit_event(
            action='reminder_restored',
            entity_id=envelope.occurrence.id,
            actor_user_id=envelope.creator.id,
            payload={
                'rule_id': envelope.rule.id,
                'scheduled_at_utc': envelope.occurrence.scheduled_at_utc.isoformat(),
                'kind': envelope.rule.kind.value,
                'recurrence_every': envelope.rule.recurrence_every,
                'recurrence_unit': envelope.rule.recurrence_unit.value if envelope.rule.recurrence_unit else None,
            },
        )
        return envelope

    async def claim_due_occurrences(self, *, limit: int) -> list[ReminderEnvelope]:
        now = datetime.now(tz=UTC)
        items = await self._reminders.claim_due_occurrences(now=now, limit=limit)
        result: list[ReminderEnvelope] = []
        for rule, occurrence in items:
            creator = await self._users.get_by_id(rule.creator_user_id)
            recipient = await self._users.get_by_id(rule.recipient_user_id)
            if creator is None or recipient is None:
                continue
            result.append(ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient))
        return result

    async def mark_delivered(self, *, occurrence_id: int, telegram_message_id: int) -> ReminderEnvelope:
        now = datetime.now(tz=UTC)
        occurrence = await self._reminders.mark_delivered(
            occurrence_id=occurrence_id,
            telegram_message_id=telegram_message_id,
            now=now,
        )
        envelope = await self._build_envelope_from_occurrence(occurrence)
        await self._schedule_next_occurrence_if_needed(envelope.rule, occurrence, now=now)
        await self._audit_event(
            action='reminder_delivered',
            entity_id=envelope.occurrence.id,
            actor_user_id=envelope.creator.id,
            payload={
                'telegram_message_id': telegram_message_id,
                'recipient_user_id': envelope.recipient.id,
                'kind': envelope.rule.kind.value,
            },
        )
        return envelope

    async def mark_delivery_failure(self, *, occurrence_id: int, error_text: str) -> ReminderDeliveryFailure:
        current = await self._reminders.get_occurrence(occurrence_id)
        if current is None:
            raise NotFoundError("Напоминание не найдено.")
        final_failure = current.delivery_attempts_count >= self._settings.reminder_max_retries
        next_attempt_at = None
        if not final_failure:
            backoff_seconds = self._settings.reminder_retry_base_seconds * (2 ** max(current.delivery_attempts_count - 1, 0))
            next_attempt_at = datetime.now(tz=UTC) + timedelta(seconds=backoff_seconds)
        occurrence = await self._reminders.mark_delivery_failure(
            occurrence_id=occurrence_id,
            error_text=error_text[:500],
            final_failure=final_failure,
            next_attempt_at_utc=next_attempt_at,
            now=datetime.now(tz=UTC),
        )
        envelope = await self._build_envelope_from_occurrence(occurrence)
        await self._audit_event(
            action='reminder_failed' if final_failure else 'reminder_retry_scheduled',
            entity_id=envelope.occurrence.id,
            actor_user_id=envelope.creator.id,
            payload={
                'recipient_user_id': envelope.recipient.id,
                'final_failure': final_failure,
                'last_error': envelope.occurrence.last_error,
                'next_attempt_at_utc': envelope.occurrence.next_attempt_at_utc.isoformat(),
            },
        )
        return ReminderDeliveryFailure(
            occurrence=envelope.occurrence,
            creator=envelope.creator,
            recipient=envelope.recipient,
            final_failure=final_failure,
        )

    async def acknowledge(self, *, telegram_user_id: int, occurrence_id: int) -> ReminderEnvelope:
        actor = await self._require_user(telegram_user_id)
        try:
            occurrence = await self._reminders.acknowledge(
                occurrence_id=occurrence_id,
                actor_user_id=actor.id,
                action="done",
                now=datetime.now(tz=UTC),
            )
        except LookupError as error:
            raise NotFoundError("Напоминание не найдено.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ConflictError(str(error)) from error
        envelope = await self._build_envelope_from_occurrence(occurrence)
        await self._audit_event(
            action='reminder_acknowledged',
            entity_id=envelope.occurrence.id,
            actor_user_id=actor.id,
            payload={'creator_user_id': envelope.creator.id},
        )
        return envelope

    async def snooze(self, *, telegram_user_id: int, occurrence_id: int, minutes: int = 10) -> tuple[ReminderEnvelope, ReminderEnvelope]:
        actor = await self._require_user(telegram_user_id)
        if minutes <= 0 or minutes > 120:
            raise ValidationError("Отложенное напоминание поддерживает диапазон от 1 до 120 минут.")
        try:
            current, follow_up = await self._reminders.snooze(
                occurrence_id=occurrence_id,
                actor_user_id=actor.id,
                minutes=minutes,
                now=datetime.now(tz=UTC),
            )
        except LookupError as error:
            raise NotFoundError("Напоминание не найдено.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ConflictError(str(error)) from error
        current_envelope = await self._build_envelope_from_occurrence(current)
        follow_up_envelope = await self._build_envelope_from_occurrence(follow_up)
        await self._audit_event(
            action='reminder_snoozed',
            entity_id=current_envelope.occurrence.id,
            actor_user_id=actor.id,
            payload={
                'follow_up_occurrence_id': follow_up_envelope.occurrence.id,
                'minutes': minutes,
            },
        )
        return current_envelope, follow_up_envelope


    async def recover_stale_processing(self) -> int:
        recovered = await self._reminders.requeue_stale_processing(
            now=datetime.now(tz=UTC),
            stale_before=datetime.now(tz=UTC) - timedelta(seconds=self._settings.processing_stale_seconds),
            max_retries=self._settings.reminder_max_retries,
            retry_base_seconds=self._settings.reminder_retry_base_seconds,
        )
        if recovered:
            await self._audit_event(
                action='reminder_recovered_stale_processing',
                entity_id='bulk',
                payload={'count': recovered},
            )
        return recovered

    async def _build_envelope_from_occurrence(self, occurrence: ReminderOccurrence) -> ReminderEnvelope:
        rule = await self._reminders.get_rule(occurrence.rule_id)
        if rule is None:
            raise NotFoundError("Правило напоминания не найдено.")
        creator = await self._users.get_by_id(occurrence.creator_user_id)
        recipient = await self._users.get_by_id(occurrence.recipient_user_id)
        if creator is None or recipient is None:
            raise NotFoundError("Участники напоминания не найдены.")
        return ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient)

    async def _schedule_next_occurrence_if_needed(
        self,
        rule: ReminderRule,
        occurrence: ReminderOccurrence,
        *,
        now: datetime,
    ) -> ReminderOccurrence | None:
        if rule.status is not ReminderRuleStatus.ACTIVE:
            return None
        if rule.kind is ReminderRuleKind.ONE_TIME:
            return None
        next_scheduled_at = _compute_next_occurrence(rule, occurrence.scheduled_at_utc)
        if await self._reminders.occurrence_exists(rule_id=rule.id, scheduled_at_utc=next_scheduled_at):
            return None
        next_occurrence = await self._reminders.create_occurrence(rule=rule, scheduled_at_utc=next_scheduled_at, now=now)
        await self._audit_event(
            action='reminder_recurring_instance_created',
            entity_id=next_occurrence.id,
            actor_user_id=rule.creator_user_id,
            payload={'rule_id': rule.id, 'kind': rule.kind.value, 'scheduled_at_utc': next_scheduled_at.isoformat()},
        )
        return next_occurrence

    async def _find_recent_duplicate_reminder(
        self,
        *,
        pair_id: int,
        creator: User,
        recipient: User,
        text: str,
        timezone: str,
        scheduled_at_utc: datetime,
        kind: ReminderRuleKind,
        recurrence_every: int,
        recurrence_unit: ReminderIntervalUnit | None,
        now: datetime,
    ) -> ReminderEnvelope | None:
        cutoff = now - _DUPLICATE_SUBMIT_WINDOW
        for rule, occurrence in await self._reminders.list_for_pair(pair_id, limit=10):
            if rule.created_at < cutoff:
                continue
            if rule.creator_user_id != creator.id or rule.recipient_user_id != recipient.id:
                continue
            if rule.status is not ReminderRuleStatus.ACTIVE:
                continue
            if occurrence.status not in {
                ReminderOccurrenceStatus.SCHEDULED,
                ReminderOccurrenceStatus.PROCESSING,
            }:
                continue
            if occurrence.text != text:
                continue
            if rule.creator_timezone != timezone or rule.origin_scheduled_at_utc != scheduled_at_utc:
                continue
            if rule.kind is not kind:
                continue
            if rule.recurrence_every != recurrence_every or rule.recurrence_unit != recurrence_unit:
                continue
            return ReminderEnvelope(rule=rule, occurrence=occurrence, creator=creator, recipient=recipient)
        return None


    async def _audit_event(
        self,
        *,
        action: str,
        entity_id: str | int | None,
        actor_user_id: int | None = None,
        payload: dict | None = None,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            action=action,
            entity_type='reminder_occurrence',
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            payload=payload,
        )

    async def _require_user(self, telegram_user_id: int) -> User:
        user = await self._users.get_by_telegram_user_id(telegram_user_id)
        if user is None:
            raise NotFoundError("Пользователь ещё не зарегистрирован. Нажми /start в боте.")
        return user

    async def _require_active_pair(self, user: User) -> tuple:
        pair = await self._pairing.get_active_pair_for_user(user.id)
        if pair is None:
            raise ConflictError("Сначала нужна активная подтверждённая пара.")
        partner = await self._users.get_by_id(pair.partner_id_for(user.id))
        if partner is None:
            raise NotFoundError("Партнёр не найден.")
        return pair, partner



def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())



def _parse_local_datetime_to_utc(value: str, timezone_name: str) -> datetime:
    if not value:
        raise ValidationError("Нужно указать дату и время напоминания.")
    try:
        local_naive = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValidationError("Некорректный формат даты и времени.") from error
    if local_naive.tzinfo is not None:
        aware = local_naive.astimezone(UTC)
        return aware
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValidationError("Неизвестный часовой пояс.") from error
    aware = local_naive.replace(tzinfo=tz)
    return aware.astimezone(UTC)


def _compute_next_occurrence(rule: ReminderRule, scheduled_at_utc: datetime) -> datetime:
    try:
        tz = ZoneInfo(rule.creator_timezone)
    except ZoneInfoNotFoundError as error:
        raise ValidationError('Неизвестный часовой пояс создателя напоминания.') from error
    local_dt = scheduled_at_utc.astimezone(tz)
    if rule.kind is ReminderRuleKind.DAILY:
        next_local = local_dt + timedelta(days=1)
    elif rule.kind is ReminderRuleKind.WEEKLY:
        next_local = local_dt + timedelta(days=7)
    elif rule.kind is ReminderRuleKind.WEEKDAYS:
        next_local = local_dt + timedelta(days=1)
        while next_local.weekday() >= 5:
            next_local += timedelta(days=1)
    elif rule.kind is ReminderRuleKind.INTERVAL:
        if rule.recurrence_unit is ReminderIntervalUnit.HOUR:
            next_local = local_dt + timedelta(hours=rule.recurrence_every)
        elif rule.recurrence_unit is ReminderIntervalUnit.DAY:
            next_local = local_dt + timedelta(days=rule.recurrence_every)
        elif rule.recurrence_unit is ReminderIntervalUnit.WEEK:
            next_local = local_dt + timedelta(weeks=rule.recurrence_every)
        elif rule.recurrence_unit is ReminderIntervalUnit.MONTH:
            next_local = _add_months(local_dt, rule.recurrence_every)
        else:
            raise ValidationError('Для интервального напоминания не задана единица повторения.')
    else:
        raise ValidationError('Для этого типа напоминания повтор не поддерживается.')
    return next_local.astimezone(UTC)


def _add_months(value: datetime, months: int) -> datetime:
    total_month = (value.month - 1) + months
    year = value.year + total_month // 12
    month = total_month % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _normalize_recurrence(
    kind: ReminderRuleKind,
    recurrence_every: int,
    recurrence_unit: ReminderIntervalUnit | None,
) -> tuple[int, ReminderIntervalUnit | None]:
    every = int(recurrence_every) if recurrence_every is not None else 1
    if every < 1:
        raise ValidationError('Интервал повторения должен быть не меньше 1.')
    if kind is ReminderRuleKind.ONE_TIME:
        return 1, None
    if kind is ReminderRuleKind.DAILY:
        return 1, ReminderIntervalUnit.DAY
    if kind is ReminderRuleKind.WEEKDAYS:
        return 1, ReminderIntervalUnit.DAY
    if kind is ReminderRuleKind.WEEKLY:
        return 1, ReminderIntervalUnit.WEEK
    if kind is ReminderRuleKind.INTERVAL:
        if recurrence_unit is None:
            raise ValidationError('Для своего интервала нужно выбрать единицу повторения.')
        if every > 365:
            raise ValidationError('Слишком большой интервал повторения.')
        return every, recurrence_unit
    raise ValidationError('Некорректный тип напоминания.')


def reminder_kind_label(
    kind: ReminderRuleKind,
    *,
    recurrence_every: int = 1,
    recurrence_unit: ReminderIntervalUnit | None = None,
) -> str:
    if kind is ReminderRuleKind.ONE_TIME:
        return 'Один раз'
    if kind is ReminderRuleKind.DAILY:
        return 'Каждый день'
    if kind is ReminderRuleKind.WEEKDAYS:
        return 'По будням'
    if kind is ReminderRuleKind.WEEKLY:
        return 'Раз в неделю'
    if kind is ReminderRuleKind.INTERVAL:
        every = max(1, int(recurrence_every or 1))
        unit = recurrence_unit.value if isinstance(recurrence_unit, ReminderIntervalUnit) else str(recurrence_unit or '')
        labels = {
            'hour': ('час', 'часа', 'часов'),
            'day': ('день', 'дня', 'дней'),
            'week': ('неделю', 'недели', 'недель'),
            'month': ('месяц', 'месяца', 'месяцев'),
        }
        word = labels.get(unit, ('единицу', 'единицы', 'единиц'))
        return f'Каждые {every} {_plural_ru(every, *word)}'
    return kind.value


def _plural_ru(number: int, one: str, few: str, many: str) -> str:
    n = abs(number) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return many
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many
