from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.errors import NotFoundError
from app.schemas.topics import (
    ClusterAssignmentOut,
    TopicListItem,
    TopicOut,
)
from app.services.topic_repository import (
    ClusterAssignmentRepository,
    TopicRepository,
)

router = APIRouter()


@router.get(
    "/topics",
    response_model=list[TopicListItem],
)
async def list_topics(
    workspace_id: UUID | None = Query(None, description="Фильтр по workspace_id"),
    is_active: bool | None = Query(
        True, description="Фильтр по активным топикам (по умолчанию только активные)"
    ),
    db: AsyncSession = Depends(get_db),
) -> list[TopicListItem]:
    """
    Получает список топиков.

    По умолчанию возвращает только активные топики.
    Можно указать workspace_id для фильтрации по рабочему пространству.
    """
    repo = TopicRepository(db)
    topics = await repo.list_topics(workspace_id=workspace_id, is_active=is_active)
    return [TopicListItem.model_validate(t) for t in topics]


@router.get(
    "/topics/{topic_key}",
    response_model=TopicOut,
)
async def get_topic(
    topic_key: str,
    workspace_id: UUID | None = Query(None, description="Фильтр по workspace_id"),
    include_profile: bool = Query(
        True, description="Включить topic_profile_json в ответ"
    ),
    db: AsyncSession = Depends(get_db),
) -> TopicOut:
    """
    Получает детальную информацию о топике по его ключу.

    Возвращает полную информацию, включая topic_profile_json (если include_profile=true).
    """
    repo = TopicRepository(db)
    topic = await repo.get_topic(topic_key=topic_key, workspace_id=workspace_id)
    if not topic:
        raise NotFoundError("Topic", topic_key)
    return TopicOut.model_validate(topic)


@router.get(
    "/cluster-assignments",
    response_model=list[ClusterAssignmentOut],
)
async def list_cluster_assignments(
    doc_version_id: UUID = Query(..., description="ID версии документа"),
    db: AsyncSession = Depends(get_db),
) -> list[ClusterAssignmentOut]:
    """
    Получает список привязок кластеров к топикам для версии документа.

    Включает mapping_debug_json для отладки маппинга.
    """
    repo = ClusterAssignmentRepository(db)
    assignments = await repo.get_assignments_by_doc_version(doc_version_id)
    return [ClusterAssignmentOut.model_validate(a) for a in assignments]


@router.get(
    "/cluster-assignments/{cluster_id}",
    response_model=ClusterAssignmentOut,
)
async def get_cluster_assignment(
    cluster_id: int = Path(..., description="ID кластера"),
    doc_version_id: UUID = Query(..., description="ID версии документа"),
    db: AsyncSession = Depends(get_db),
) -> ClusterAssignmentOut:
    """
    Получает привязку кластера к топику по doc_version_id и cluster_id.

    Включает mapping_debug_json для отладки маппинга.
    """
    repo = ClusterAssignmentRepository(db)
    assignment = await repo.get_assignment(doc_version_id, cluster_id)
    if not assignment:
        raise NotFoundError("ClusterAssignment", f"{doc_version_id}:{cluster_id}")
    return ClusterAssignmentOut.model_validate(assignment)

