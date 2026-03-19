from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import (
    CareDispatchStatus,
    EphemeralMessageStatus,
    InviteStatus,
    PairStatus,
    ReminderOccurrenceStatus,
    ReminderRuleKind,
    ReminderRuleStatus,
)


@dataclass(slots=True, frozen=True)
class User:
    id: int
    telegram_user_id: int
    private_chat_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    timezone: str
    started_bot: bool
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None

    @property
    def display_name(self) -> str:
        if self.first_name and self.last_name:
            return f'{self.first_name} {self.last_name}'.strip()
        if self.first_name:
            return self.first_name
        if self.username:
            return f'@{self.username}'
        return f'user_{self.telegram_user_id}'


@dataclass(slots=True, frozen=True)
class PairInvite:
    id: int
    inviter_user_id: int
    invitee_user_id: int | None
    token_hash: str
    status: InviteStatus
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class Pair:
    id: int
    user_a_id: int
    user_b_id: int
    status: PairStatus
    created_by_user_id: int
    confirmed_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def includes(self, user_id: int) -> bool:
        return self.user_a_id == user_id or self.user_b_id == user_id

    def partner_id_for(self, user_id: int) -> int:
        if self.user_a_id == user_id:
            return self.user_b_id
        if self.user_b_id == user_id:
            return self.user_a_id
        raise ValueError(f'User {user_id} does not belong to pair {self.id}.')


@dataclass(slots=True, frozen=True)
class DashboardState:
    user: User
    active_pair: Pair | None
    partner: User | None
    outgoing_invite: PairInvite | None
    incoming_invite: PairInvite | None
    incoming_inviter: User | None

    @property
    def mode(self) -> str:
        if self.active_pair is not None:
            return 'active'
        if self.incoming_invite is not None:
            return 'incoming_invite'
        if self.outgoing_invite is not None:
            return 'outgoing_invite'
        return 'no_pair'


@dataclass(slots=True, frozen=True)
class PairInvitePreview:
    invite: PairInvite
    inviter: User


@dataclass(slots=True, frozen=True)
class ReminderRule:
    id: int
    pair_id: int
    creator_user_id: int
    recipient_user_id: int
    kind: ReminderRuleKind
    text: str
    creator_timezone: str
    origin_scheduled_at_utc: datetime
    status: ReminderRuleStatus
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class ReminderOccurrence:
    id: int
    rule_id: int
    pair_id: int
    creator_user_id: int
    recipient_user_id: int
    text: str
    scheduled_at_utc: datetime
    next_attempt_at_utc: datetime
    status: ReminderOccurrenceStatus
    handled_action: str | None
    telegram_message_id: int | None
    delivery_attempts_count: int
    last_error: str | None
    sent_at: datetime | None
    delivered_at: datetime | None
    acknowledged_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class ReminderEnvelope:
    rule: ReminderRule
    occurrence: ReminderOccurrence
    creator: User
    recipient: User


@dataclass(slots=True, frozen=True)
class CareTemplate:
    id: int
    template_code: str
    category: str
    category_label: str
    title: str
    body: str
    emoji: str
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class CareDispatch:
    id: int
    pair_id: int
    sender_user_id: int
    recipient_user_id: int
    template_code: str
    category: str
    category_label: str
    title: str
    body: str
    emoji: str
    status: CareDispatchStatus
    telegram_message_id: int | None
    response_code: str | None
    response_title: str | None
    response_body: str | None
    response_emoji: str | None
    response_clicked_at: datetime | None
    next_attempt_at_utc: datetime | None
    processing_started_at: datetime | None
    delivery_attempts_count: int
    sent_at: datetime | None
    delivered_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class CareEnvelope:
    dispatch: CareDispatch
    sender: User
    recipient: User


@dataclass(slots=True, frozen=True)
class UIPanel:
    id: int
    user_id: int
    panel_key: str
    chat_id: int
    message_id: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class EphemeralMessage:
    id: int
    chat_id: int
    message_id: int
    kind: str
    delete_after_utc: datetime
    status: EphemeralMessageStatus
    attempts_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class AuditLog:
    id: int
    actor_user_id: int | None
    entity_type: str
    entity_id: str | None
    action: str
    payload_json: str | None
    request_id: str | None
    created_at: datetime


@dataclass(slots=True, frozen=True)
class TelegramUserSnapshot:
    telegram_user_id: int
    chat_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
