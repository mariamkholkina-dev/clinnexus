from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditLog

"""
Вспомогательные функции для audit logging.
"""


async def log_audit(
    db: AsyncSession,
    workspace_id: UUID,
    action: str,
    entity_type: str,
    entity_id: str,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    actor_user_id: UUID | None = None,
) -> None:
    """
    Логирует действие в audit_log.

    Args:
        db: Сессия базы данных
        workspace_id: ID рабочего пространства
        action: Действие (create, update, delete, upload и т.д.)
        entity_type: Тип сущности (study, document, document_version и т.д.)
        entity_id: ID сущности (строка)
        before_json: Состояние до изменения (опционально)
        after_json: Состояние после изменения (опционально)
        actor_user_id: ID пользователя, выполнившего действие (опционально)
    """
    audit_entry = AuditLog(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=before_json,
        after_json=after_json,
    )
    db.add(audit_entry)
    # Не коммитим здесь, чтобы не создавать отдельную транзакцию
    # Коммит должен быть выполнен вызывающим кодом

