from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from nimarita.logging import get_request_id
from nimarita.repositories.audit import AuditRepository

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str | int | None = None,
        actor_user_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self._repository.append(
                actor_user_id=actor_user_id,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                action=action,
                payload=payload,
                request_id=get_request_id(),
                now=datetime.now(tz=UTC),
            )
        except Exception:
            logger.exception('Failed to write audit log action=%s entity_type=%s entity_id=%s', action, entity_type, entity_id)
