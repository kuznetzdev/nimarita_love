from __future__ import annotations

from enum import Enum


class PairStatus(str, Enum):
    ACTIVE = 'active'
    CLOSED = 'closed'


class InviteStatus(str, Enum):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'
    EXPIRED = 'expired'


class ReminderRuleKind(str, Enum):
    ONE_TIME = 'one_time'


class ReminderRuleStatus(str, Enum):
    ACTIVE = 'active'
    CANCELLED = 'cancelled'


class ReminderOccurrenceStatus(str, Enum):
    SCHEDULED = 'scheduled'
    PROCESSING = 'processing'
    DELIVERED = 'delivered'
    ACKNOWLEDGED = 'acknowledged'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class CareDispatchStatus(str, Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    SENT = 'sent'
    RESPONDED = 'responded'
    FAILED = 'failed'


class EphemeralMessageStatus(str, Enum):
    PENDING = 'pending'
    DELETED = 'deleted'
    FAILED = 'failed'
