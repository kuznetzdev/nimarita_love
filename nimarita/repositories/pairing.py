from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
from typing import Any

from nimarita.domain.enums import InviteStatus, PairStatus
from nimarita.domain.models import Pair, PairInvite
from nimarita.infra.sqlite import SQLiteDatabase, SQLiteTransaction


class PairingRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def expire_due_invites(self, now: datetime) -> int:
        async with self._db.transaction() as tx:
            cursor = await tx.execute(
                """
                UPDATE pair_invites
                SET status = ?, updated_at = ?
                WHERE status = ? AND expires_at <= ?
                """,
                (
                    InviteStatus.EXPIRED.value,
                    now.isoformat(),
                    InviteStatus.PENDING.value,
                    now.isoformat(),
                ),
            )
            return int(cursor.rowcount)

    async def get_active_pair_for_user(self, user_id: int) -> Pair | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM pairs
            WHERE status = ? AND (user_a_id = ? OR user_b_id = ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (PairStatus.ACTIVE.value, user_id, user_id),
        )
        return _row_to_pair(row) if row is not None else None

    async def get_latest_pending_outgoing_invite(self, user_id: int) -> PairInvite | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM pair_invites
            WHERE inviter_user_id = ? AND status = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, InviteStatus.PENDING.value),
        )
        return _row_to_invite(row) if row is not None else None

    async def cancel_latest_pending_outgoing_invite(self, *, inviter_user_id: int, now: datetime) -> PairInvite | None:
        async with self._db.transaction() as tx:
            invite_row = await tx.fetchone(
                """
                SELECT * FROM pair_invites
                WHERE inviter_user_id = ? AND status = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (inviter_user_id, InviteStatus.PENDING.value),
            )
            if invite_row is None:
                return None
            invite = _row_to_invite(invite_row)
            await tx.execute(
                """
                UPDATE pair_invites
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    InviteStatus.EXPIRED.value,
                    now.isoformat(),
                    invite.id,
                ),
            )
            cancelled_row = await tx.fetchone("SELECT * FROM pair_invites WHERE id = ?", (invite.id,))
            assert cancelled_row is not None
            return _row_to_invite(cancelled_row)

    async def get_latest_pending_incoming_invite(self, user_id: int) -> PairInvite | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM pair_invites
            WHERE invitee_user_id = ? AND status = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, InviteStatus.PENDING.value),
        )
        return _row_to_invite(row) if row is not None else None

    async def bind_pending_invite_to_user(self, invite_id: int, invitee_user_id: int, now: datetime) -> PairInvite:
        async with self._db.transaction() as tx:
            invite_row = await tx.fetchone(
                "SELECT * FROM pair_invites WHERE id = ? AND status = ?",
                (invite_id, InviteStatus.PENDING.value),
            )
            if invite_row is None:
                raise LookupError("Приглашение уже недоступно.")
            invite = _row_to_invite(invite_row)
            if invite.inviter_user_id == invitee_user_id:
                raise ValueError("Нельзя открыть собственное приглашение как входящее.")
            if invite.invitee_user_id is not None and invite.invitee_user_id != invitee_user_id:
                raise PermissionError("Приглашение уже закреплено за другим пользователем.")
            if invite.invitee_user_id is None:
                await tx.execute(
                    "UPDATE pair_invites SET invitee_user_id = ?, updated_at = ? WHERE id = ?",
                    (invitee_user_id, now.isoformat(), invite_id),
                )
            bound_row = await tx.fetchone("SELECT * FROM pair_invites WHERE id = ?", (invite_id,))
            assert bound_row is not None
            return _row_to_invite(bound_row)

    async def expire_pending_for_users(self, *, user_ids: tuple[int, ...], now: datetime, exclude_invite_id: int | None = None) -> int:
        if not user_ids:
            return 0
        unique_user_ids = tuple(dict.fromkeys(user_ids))
        inviter_placeholders = ",".join("?" for _ in unique_user_ids)
        invitee_placeholders = ",".join("?" for _ in unique_user_ids)
        conditions = [
            "status = ?",
            f"(inviter_user_id IN ({inviter_placeholders}) OR invitee_user_id IN ({invitee_placeholders}))",
        ]
        parameters: list[object] = [InviteStatus.PENDING.value, *unique_user_ids, *unique_user_ids]
        if exclude_invite_id is not None:
            conditions.append("id != ?")
            parameters.append(exclude_invite_id)
        query = (
            "UPDATE pair_invites SET status = ?, updated_at = ? WHERE " + " AND ".join(conditions)
        )
        async with self._db.transaction() as tx:
            cursor = await tx.execute(
                query,
                (
                    InviteStatus.EXPIRED.value,
                    now.isoformat(),
                    *parameters,
                ),
            )
            return int(cursor.rowcount)

    async def get_pending_invite_by_id(self, invite_id: int) -> PairInvite | None:
        row = await self._db.fetchone(
            "SELECT * FROM pair_invites WHERE id = ? AND status = ?",
            (invite_id, InviteStatus.PENDING.value),
        )
        return _row_to_invite(row) if row is not None else None

    async def get_pending_invite_by_token_hash(self, token_hash: str) -> PairInvite | None:
        row = await self._db.fetchone(
            "SELECT * FROM pair_invites WHERE token_hash = ? AND status = ?",
            (token_hash, InviteStatus.PENDING.value),
        )
        return _row_to_invite(row) if row is not None else None

    async def create_invite(
        self,
        inviter_user_id: int,
        token_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> PairInvite:
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                UPDATE pair_invites
                SET status = ?, updated_at = ?
                WHERE inviter_user_id = ? AND status = ?
                """,
                (
                    InviteStatus.EXPIRED.value,
                    now.isoformat(),
                    inviter_user_id,
                    InviteStatus.PENDING.value,
                ),
            )
            cursor = await tx.execute(
                """
                INSERT INTO pair_invites (
                    inviter_user_id,
                    invitee_user_id,
                    token_hash,
                    status,
                    expires_at,
                    consumed_at,
                    created_at,
                    updated_at
                ) VALUES (?, NULL, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    inviter_user_id,
                    token_hash,
                    InviteStatus.PENDING.value,
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            invite_id = int(cursor.lastrowid)
            row = await tx.fetchone("SELECT * FROM pair_invites WHERE id = ?", (invite_id,))
            assert row is not None
            return _row_to_invite(row)

    async def accept_invite(self, invite_id: int, invitee_user_id: int, now: datetime) -> tuple[PairInvite, Pair]:
        try:
            async with self._db.transaction() as tx:
                invite_row = await tx.fetchone(
                    "SELECT * FROM pair_invites WHERE id = ? AND status = ?",
                    (invite_id, InviteStatus.PENDING.value),
                )
                if invite_row is None:
                    raise LookupError("Приглашение уже не ожидает подтверждения.")
                invite = _row_to_invite(invite_row)
                if invite.invitee_user_id is not None and invite.invitee_user_id != invitee_user_id:
                    raise PermissionError("Приглашение уже закреплено за другим пользователем.")
                pair = await _create_pair_with_checks(tx, invite=invite, invitee_user_id=invitee_user_id, now=now)
                await tx.execute(
                    """
                    UPDATE pair_invites
                    SET invitee_user_id = ?, status = ?, consumed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        invitee_user_id,
                        InviteStatus.ACCEPTED.value,
                        now.isoformat(),
                        now.isoformat(),
                        invite_id,
                    ),
                )
                await tx.execute(
                    """
                    UPDATE pair_invites
                    SET status = ?, updated_at = ?
                    WHERE status = ?
                      AND id != ?
                      AND (inviter_user_id IN (?, ?) OR invitee_user_id IN (?, ?))
                    """,
                    (
                        InviteStatus.EXPIRED.value,
                        now.isoformat(),
                        InviteStatus.PENDING.value,
                        invite_id,
                        invite.inviter_user_id,
                        invitee_user_id,
                        invite.inviter_user_id,
                        invitee_user_id,
                    ),
                )
                accepted_row = await tx.fetchone("SELECT * FROM pair_invites WHERE id = ?", (invite_id,))
                assert accepted_row is not None
                return _row_to_invite(accepted_row), pair
        except sqlite3.IntegrityError as error:
            raise ValueError(str(error)) from error

    async def reject_invite(self, invite_id: int, invitee_user_id: int, now: datetime) -> PairInvite:
        async with self._db.transaction() as tx:
            invite_row = await tx.fetchone(
                "SELECT * FROM pair_invites WHERE id = ? AND status = ?",
                (invite_id, InviteStatus.PENDING.value),
            )
            if invite_row is None:
                raise LookupError("Приглашение уже не ожидает подтверждения.")
            invite = _row_to_invite(invite_row)
            if invite.invitee_user_id is not None and invite.invitee_user_id != invitee_user_id:
                raise PermissionError("Приглашение уже закреплено за другим пользователем.")
            await tx.execute(
                """
                UPDATE pair_invites
                SET invitee_user_id = ?, status = ?, consumed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    invitee_user_id,
                    InviteStatus.REJECTED.value,
                    now.isoformat(),
                    now.isoformat(),
                    invite_id,
                ),
            )
            rejected_row = await tx.fetchone("SELECT * FROM pair_invites WHERE id = ?", (invite_id,))
            assert rejected_row is not None
            return _row_to_invite(rejected_row)

    async def close_active_pair_for_user(self, user_id: int, now: datetime) -> Pair | None:
        async with self._db.transaction() as tx:
            pair_row = await tx.fetchone(
                """
                SELECT * FROM pairs
                WHERE status = ? AND (user_a_id = ? OR user_b_id = ?)
                ORDER BY id DESC
                LIMIT 1
                """,
                (PairStatus.ACTIVE.value, user_id, user_id),
            )
            if pair_row is None:
                return None
            pair = _row_to_pair(pair_row)
            await tx.execute(
                """
                UPDATE pairs
                SET status = ?, closed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    PairStatus.CLOSED.value,
                    now.isoformat(),
                    now.isoformat(),
                    pair.id,
                ),
            )
            closed_row = await tx.fetchone("SELECT * FROM pairs WHERE id = ?", (pair.id,))
            assert closed_row is not None
            return _row_to_pair(closed_row)


async def _create_pair_with_checks(
    tx: SQLiteTransaction,
    *,
    invite: PairInvite,
    invitee_user_id: int,
    now: datetime,
) -> Pair:
    if invite.inviter_user_id == invitee_user_id:
        raise ValueError("Нельзя принять собственное приглашение.")

    inviter_row = await tx.fetchone("SELECT * FROM users WHERE id = ?", (invite.inviter_user_id,))
    invitee_row = await tx.fetchone("SELECT * FROM users WHERE id = ?", (invitee_user_id,))
    if inviter_row is None or invitee_row is None:
        raise ValueError("Данные пользователя не найдены.")
    if not bool(inviter_row["started_bot"]) or not bool(invitee_row["started_bot"]):
        raise ValueError("Оба пользователя должны хотя бы один раз запустить бота перед подтверждением пары.")

    active_for_inviter = await tx.fetchone(
        "SELECT 1 FROM pairs WHERE status = ? AND (user_a_id = ? OR user_b_id = ?) LIMIT 1",
        (PairStatus.ACTIVE.value, invite.inviter_user_id, invite.inviter_user_id),
    )
    active_for_invitee = await tx.fetchone(
        "SELECT 1 FROM pairs WHERE status = ? AND (user_a_id = ? OR user_b_id = ?) LIMIT 1",
        (PairStatus.ACTIVE.value, invitee_user_id, invitee_user_id),
    )
    if active_for_inviter is not None or active_for_invitee is not None:
        raise ValueError("Один из пользователей уже состоит в активной паре.")

    user_a_id, user_b_id = sorted((invite.inviter_user_id, invitee_user_id))
    cursor = await tx.execute(
        """
        INSERT INTO pairs (
            user_a_id,
            user_b_id,
            status,
            created_by_user_id,
            confirmed_at,
            closed_at,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            user_a_id,
            user_b_id,
            PairStatus.ACTIVE.value,
            invite.inviter_user_id,
            now.isoformat(),
            now.isoformat(),
            now.isoformat(),
        ),
    )
    pair_id = int(cursor.lastrowid)
    pair_row = await tx.fetchone("SELECT * FROM pairs WHERE id = ?", (pair_id,))
    assert pair_row is not None
    return _row_to_pair(pair_row)



def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)



def _row_to_invite(row: Any) -> PairInvite:
    return PairInvite(
        id=int(row["id"]),
        inviter_user_id=int(row["inviter_user_id"]),
        invitee_user_id=int(row["invitee_user_id"]) if row["invitee_user_id"] is not None else None,
        token_hash=row["token_hash"],
        status=InviteStatus(row["status"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        consumed_at=_parse_datetime(row["consumed_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )



def _row_to_pair(row: Any) -> Pair:
    return Pair(
        id=int(row["id"]),
        user_a_id=int(row["user_a_id"]),
        user_b_id=int(row["user_b_id"]),
        status=PairStatus(row["status"]),
        created_by_user_id=int(row["created_by_user_id"]),
        confirmed_at=_parse_datetime(row["confirmed_at"]),
        closed_at=_parse_datetime(row["closed_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
