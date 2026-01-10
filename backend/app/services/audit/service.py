"""Сервис для оркестрации всех аудиторов."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.logging import logger
from app.db.enums import AuditStatus
from app.db.models.audit import AuditIssue as AuditIssueModel
from app.db.models.studies import Document, DocumentType, DocumentVersion, Study
from app.schemas.audit import AuditIssue, AuditRunResult
from app.services.audit.cross.protocol_csr import ProtocolCsrConsistencyAuditor
from app.services.audit.cross.protocol_icf import ProtocolIcfConsistencyAuditor
from app.services.audit.intra.abbreviations import AbbreviationAuditor
from app.services.audit.intra.consistency import ConsistencyAuditor
from app.services.audit.intra.placeholder import PlaceholderAuditor
from app.services.audit.intra.visit_logic import VisitLogicAuditor


class AuditService:
    """Сервис для запуска всех аудиторов и сохранения результатов."""

    def __init__(self, db: AsyncSession, study_id: UUID) -> None:
        """Инициализация сервиса.

        Args:
            db: Асинхронная сессия БД
            study_id: ID исследования
        """
        self.db = db
        self.study_id = study_id

    async def run_intra_document_audit(self, doc_version_id: UUID) -> list[AuditRunResult]:
        """Запускает все внутридокументные аудиторы для одного документа.

        Args:
            doc_version_id: ID версии документа для проверки

        Returns:
            Список результатов для каждого аудитора
        """
        logger.info(f"Запуск внутридокументного аудита для doc_version_id={doc_version_id}")

        # Список внутридокументных аудиторов
        intra_auditors = [
            ConsistencyAuditor(self.db, self.study_id),
            AbbreviationAuditor(self.db, self.study_id),
            VisitLogicAuditor(self.db, self.study_id),
            PlaceholderAuditor(self.db, self.study_id),
        ]

        results: list[AuditRunResult] = []

        for auditor in intra_auditors:
            try:
                logger.info(f"Запуск аудитора: {auditor.name}")
                issues = await auditor.run(doc_version_id)

                # Сохраняем issues в БД
                await self._save_issues(doc_version_id, issues, auditor.name)

                results.append(
                    AuditRunResult(
                        doc_version_id=doc_version_id,
                        auditor_name=auditor.name,
                        issues_count=len(issues),
                        issues=issues,
                    )
                )

                logger.info(f"Аудитор {auditor.name} завершен: найдено {len(issues)} проблем")
            except Exception as e:
                logger.error(f"Ошибка при выполнении аудитора {auditor.name}: {e}", exc_info=True)
                # Продолжаем работу с другими аудиторами

        return results

    async def run_cross_document_audit(
        self, primary_doc_version_id: UUID, secondary_doc_version_id: UUID
    ) -> list[AuditRunResult]:
        """Запускает кросс-документные аудиторы для пары документов.

        Args:
            primary_doc_version_id: ID версии основного документа (обычно Protocol)
            secondary_doc_version_id: ID версии вторичного документа (ICF, CSR, IB)

        Returns:
            Список результатов для каждого аудитора
        """
        logger.info(
            f"Запуск кросс-документного аудита: primary={primary_doc_version_id}, "
            f"secondary={secondary_doc_version_id}"
        )

        # Определяем тип вторичного документа
        secondary_version = await self.db.get(DocumentVersion, secondary_doc_version_id)
        if not secondary_version:
            logger.warning(f"Вторичный документ {secondary_doc_version_id} не найден")
            return []

        secondary_doc = await self.db.get(Document, secondary_version.document_id)
        if not secondary_doc:
            logger.warning(f"Документ {secondary_version.document_id} не найден")
            return []

        results: list[AuditRunResult] = []

        # Выбираем подходящие аудиторы в зависимости от типа вторичного документа
        if secondary_doc.doc_type == DocumentType.ICF:
            auditor = ProtocolIcfConsistencyAuditor(self.db, self.study_id)
            try:
                logger.info(f"Запуск кросс-документного аудитора: {auditor.name}")
                issues = await auditor.run(primary_doc_version_id, secondary_doc_version_id)

                # Сохраняем issues в БД (привязываем к обоим документам, используем primary)
                await self._save_issues(primary_doc_version_id, issues, auditor.name)

                results.append(
                    AuditRunResult(
                        doc_version_id=primary_doc_version_id,
                        auditor_name=auditor.name,
                        issues_count=len(issues),
                        issues=issues,
                    )
                )
            except Exception as e:
                logger.error(f"Ошибка при выполнении аудитора {auditor.name}: {e}", exc_info=True)

        elif secondary_doc.doc_type == DocumentType.CSR:
            auditor = ProtocolCsrConsistencyAuditor(self.db, self.study_id)
            try:
                logger.info(f"Запуск кросс-документного аудитора: {auditor.name}")
                issues = await auditor.run(primary_doc_version_id, secondary_doc_version_id)

                await self._save_issues(primary_doc_version_id, issues, auditor.name)

                results.append(
                    AuditRunResult(
                        doc_version_id=primary_doc_version_id,
                        auditor_name=auditor.name,
                        issues_count=len(issues),
                        issues=issues,
                    )
                )
            except Exception as e:
                logger.error(f"Ошибка при выполнении аудитора {auditor.name}: {e}", exc_info=True)

        return results

    async def _save_issues(
        self, doc_version_id: UUID, issues: list[AuditIssue], auditor_name: str
    ) -> None:
        """Сохраняет найденные проблемы в БД.

        Args:
            doc_version_id: ID версии документа
            issues: Список найденных проблем
            auditor_name: Имя аудитора, нашедшего проблемы
        """
        for issue in issues:
            audit_issue = AuditIssueModel(
                study_id=self.study_id,
                doc_version_id=doc_version_id,
                severity=issue.severity,
                category=issue.category,
                description=issue.description,
                location_anchors=issue.location_anchors if issue.location_anchors else None,
                status=AuditStatus.OPEN,
                suppression_reason=None,
                suggested_fix=issue.suggested_fix,
            )
            self.db.add(audit_issue)

            # Получаем workspace_id из study
            study = await self.db.get(Study, self.study_id)
            workspace_id = study.workspace_id if study else None

            # Логируем действие в audit_log
            log_audit(
                db=self.db,
                workspace_id=workspace_id,
                actor_user_id=None,
                action=f"audit_issue_created_{auditor_name}",
                entity_type="audit_issue",
                entity_id=str(audit_issue.id),
                before_json=None,
                after_json={
                    "severity": issue.severity.value,
                    "category": issue.category.value,
                    "description": issue.description,
                    "auditor_name": auditor_name,
                },
            )

        await self.db.flush()
        logger.info(f"Сохранено {len(issues)} проблем в БД для doc_version_id={doc_version_id}")

    async def get_issues_for_document(
        self, doc_version_id: UUID, status: AuditStatus | None = None
    ) -> list[AuditIssueModel]:
        """Получает все проблемы для документа.

        Args:
            doc_version_id: ID версии документа
            status: Опциональный фильтр по статусу

        Returns:
            Список проблем из БД
        """
        from sqlalchemy import select

        stmt = select(AuditIssueModel).where(
            AuditIssueModel.doc_version_id == doc_version_id, AuditIssueModel.study_id == self.study_id
        )

        if status:
            stmt = stmt.where(AuditIssueModel.status == status)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

