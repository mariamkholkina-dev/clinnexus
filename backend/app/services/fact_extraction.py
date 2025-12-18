from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AnchorContentType
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.db.models.studies import DocumentVersion, Study
from app.db.enums import EvidenceRole, FactStatus


class FactExtractionResult:
    """Результат извлечения фактов."""

    def __init__(
        self,
        doc_version_id: UUID,
        facts_count: int = 0,
        facts: list[Fact] | None = None,
    ) -> None:
        self.doc_version_id = doc_version_id
        self.facts_count = facts_count
        self.facts = facts or []


class FactExtractionService:
    """Сервис для извлечения и сохранения фактов из документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def extract_and_upsert(self, doc_version_id: UUID, *, commit: bool = True) -> FactExtractionResult:
        """
        Извлекает факты из документа и сохраняет их в БД.

        Реализация rules-first (без LLM):
        - Загружаем anchors версии документа по типам: hdr/p/li/fn
        - Сортируем: hdr первыми, затем p/li/fn, затем ordinal
        - Извлекаем минимальный набор фактов:
          - protocol_meta / protocol_version
          - protocol_meta / amendment_date
          - population / planned_n_total
        - Upsert по (study_id, fact_type, fact_key)
        - Evidence: идемпотентно заменяем (delete by fact_id + insert), anchor_id только реальный
        """
        logger.info(f"Rules-first извлечение фактов из документа {doc_version_id}")

        # Получаем версию документа
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        study_id = doc_version.document.study_id

        anchors = await self._load_anchors_for_fact_extraction(doc_version_id)

        candidates: list[_ExtractedFactCandidate] = [
            self._extract_protocol_version(anchors),
            self._extract_amendment_date(anchors),
            self._extract_planned_n_total(anchors),
        ]

        upserted: list[Fact] = []
        for cand in candidates:
            fact = await self._upsert_fact(
                study_id=study_id,
                doc_version_id=doc_version_id,
                fact_type=cand.fact_type,
                fact_key=cand.fact_key,
                value_json=cand.value_json,
                unit=cand.unit,
                status=cand.status,
            )
            await self._replace_evidence_for_fact(
                fact_id=fact.id,
                primary_anchor_ids=cand.primary_anchor_ids,
                supporting_anchor_ids=cand.supporting_anchor_ids,
                allowed_anchor_ids=cand.allowed_anchor_ids,
            )
            upserted.append(fact)

        if commit:
            await self.db.commit()
        else:
            # Для сценариев, где транзакцией управляет внешний оркестратор (например ingestion),
            # не коммитим здесь, а только флашим изменения в текущей сессии.
            await self.db.flush()

        logger.info(f"Извлечено/обновлено {len(upserted)} фактов из {doc_version_id}")
        return FactExtractionResult(doc_version_id=doc_version_id, facts_count=len(upserted), facts=upserted)

    async def _load_anchors_for_fact_extraction(self, doc_version_id: UUID) -> list[Anchor]:
        allowed_types = [
            AnchorContentType.HDR,
            AnchorContentType.P,
            AnchorContentType.LI,
            AnchorContentType.FN,
        ]
        stmt = (
            select(Anchor)
            .where(Anchor.doc_version_id == doc_version_id)
            .where(Anchor.content_type.in_(allowed_types))
        )
        res = await self.db.execute(stmt)
        anchors = res.scalars().all()

        def _type_bucket(ct: AnchorContentType) -> int:
            # hdr first, then p/li/fn
            if ct == AnchorContentType.HDR:
                return 0
            return 1

        def _type_order(ct: AnchorContentType) -> int:
            # within non-hdr: p first, then li, then fn
            return {
                AnchorContentType.P: 0,
                AnchorContentType.LI: 1,
                AnchorContentType.FN: 2,
                AnchorContentType.HDR: 0,
            }.get(ct, 9)

        anchors.sort(key=lambda a: (_type_bucket(a.content_type), _type_order(a.content_type), a.ordinal))
        return anchors

    async def _upsert_fact(
        self,
        *,
        study_id: UUID,
        doc_version_id: UUID,
        fact_type: str,
        fact_key: str,
        value_json: dict[str, Any],
        unit: str | None,
        status: FactStatus,
    ) -> Fact:
        stmt = select(Fact).where(
            Fact.study_id == study_id,
            Fact.fact_type == fact_type,
            Fact.fact_key == fact_key,
        )
        res = await self.db.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            existing.value_json = value_json
            existing.unit = unit
            existing.status = status
            existing.created_from_doc_version_id = doc_version_id
            await self.db.flush()
            return existing

        fact = Fact(
            study_id=study_id,
            fact_type=fact_type,
            fact_key=fact_key,
            value_json=value_json,
            unit=unit,
            status=status,
            created_from_doc_version_id=doc_version_id,
        )
        self.db.add(fact)
        await self.db.flush()
        return fact

    async def _replace_evidence_for_fact(
        self,
        *,
        fact_id: UUID,
        primary_anchor_ids: list[str],
        supporting_anchor_ids: list[str],
        allowed_anchor_ids: set[str],
    ) -> None:
        # Идемпотентность: удаляем старые evidence для факта и создаём новые.
        await self.db.execute(delete(FactEvidence).where(FactEvidence.fact_id == fact_id))

        primary = _dedupe_keep_order([aid for aid in primary_anchor_ids if aid in allowed_anchor_ids])
        supporting = _dedupe_keep_order([aid for aid in supporting_anchor_ids if aid in allowed_anchor_ids and aid not in set(primary)])

        for aid in primary:
            self.db.add(FactEvidence(fact_id=fact_id, anchor_id=aid, evidence_role=EvidenceRole.PRIMARY))
        for aid in supporting:
            self.db.add(FactEvidence(fact_id=fact_id, anchor_id=aid, evidence_role=EvidenceRole.SUPPORTING))

        await self.db.flush()

    def _extract_protocol_version(self, anchors: list[Anchor]) -> _ExtractedFactCandidate:
        allowed_anchor_ids = {a.anchor_id for a in anchors}
        # EN examples: "Protocol Version: 2.0", "Protocol No.: ABC-123", "Protocol Number ABC-123"
        en = re.compile(
            r"\bprotocol\s*(?:version|no\.?|number)\b\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/\-]{0,64})",
            re.IGNORECASE,
        )
        # RU examples: "Версия протокола: 2.0", "Номер протокола: ABC-123"
        ru = re.compile(
            r"\b(?:версия|номер)\s+протокола\b\s*[:#]?\s*([A-Za-z0-9А-Яа-я][A-Za-z0-9А-Яа-я._/\-]{0,64})",
            re.IGNORECASE,
        )

        for a in anchors:
            text = a.text_raw or a.text_norm
            if not text:
                continue
            m = en.search(text) or ru.search(text)
            if m:
                val = m.group(1).strip()
                if not val:
                    break
                return _ExtractedFactCandidate(
                    fact_type="protocol_meta",
                    fact_key="protocol_version",
                    value_json={"value": val},
                    unit=None,
                    status=FactStatus.EXTRACTED,
                    primary_anchor_ids=[a.anchor_id],
                    supporting_anchor_ids=[],
                    allowed_anchor_ids=allowed_anchor_ids,
                )

        return _ExtractedFactCandidate.needs_review(
            fact_type="protocol_meta",
            fact_key="protocol_version",
            allowed_anchor_ids=allowed_anchor_ids,
        )

    def _extract_amendment_date(self, anchors: list[Anchor]) -> _ExtractedFactCandidate:
        allowed_anchor_ids = {a.anchor_id for a in anchors}
        # EN: "Amendment Date: 05 March 2021", "Date of Amendment 2021-03-05"
        en = re.compile(
            r"\b(?:amendment\s+date|date\s+of\s+amendment)\b\s*[:#]?\s*(.+)$",
            re.IGNORECASE,
        )
        # RU: "Дата внесения изменений: 05.03.2021", "Дата поправки: 5 марта 2021"
        ru = re.compile(
            r"\b(?:дата\s+(?:внесения\s+изменений|поправки|изменения)|дата\s+амендмента)\b\s*[:#]?\s*(.+)$",
            re.IGNORECASE,
        )

        for a in anchors:
            text = (a.text_raw or a.text_norm or "").strip()
            if not text:
                continue
            m = en.search(text) or ru.search(text)
            if not m:
                continue
            raw = m.group(1).strip()
            raw = raw.strip(" .;")
            iso = _parse_date_to_iso(raw)
            if iso:
                return _ExtractedFactCandidate(
                    fact_type="protocol_meta",
                    fact_key="amendment_date",
                    value_json={"value": iso, "raw": raw},
                    unit=None,
                    status=FactStatus.EXTRACTED,
                    primary_anchor_ids=[a.anchor_id],
                    supporting_anchor_ids=[],
                    allowed_anchor_ids=allowed_anchor_ids,
                )
            # Если уверенно нашли поле, но не смогли распарсить дату — это review.
            return _ExtractedFactCandidate(
                fact_type="protocol_meta",
                fact_key="amendment_date",
                value_json={"value": None, "raw": raw},
                unit=None,
                status=FactStatus.NEEDS_REVIEW,
                primary_anchor_ids=[a.anchor_id],
                supporting_anchor_ids=[],
                allowed_anchor_ids=allowed_anchor_ids,
            )

        return _ExtractedFactCandidate.needs_review(
            fact_type="protocol_meta",
            fact_key="amendment_date",
            extra_value_json={"raw": None},
            allowed_anchor_ids=allowed_anchor_ids,
        )

    def _extract_planned_n_total(self, anchors: list[Anchor]) -> _ExtractedFactCandidate:
        allowed_anchor_ids = {a.anchor_id for a in anchors}
        # EN patterns: "Total N=120", "N = 120", "planned enrollment ... 120"
        en = re.compile(
            r"\b(?:total\s*n|planned\s+enrollment|target\s+enrollment|enrollment)\b[^0-9]{0,25}(\d{1,7}(?:[ ,]\d{3})*)",
            re.IGNORECASE,
        )
        en_n = re.compile(r"\bN\s*=\s*(\d{1,7}(?:[ ,]\d{3})*)\b", re.IGNORECASE)

        # RU patterns: "Всего N=120", "планируемое число ... 120", "планируется включить 120"
        ru = re.compile(
            r"\b(?:всего\s*n|общее\s+число|планируем(?:ое|ая)\s+число|планируем(?:ый|ая)\s+набор|планируется\s+включить)\b[^0-9]{0,35}(\d{1,7}(?:[ ,]\d{3})*)",
            re.IGNORECASE,
        )
        ru_n = re.compile(r"\bN\s*=\s*(\d{1,7}(?:[ ,]\d{3})*)\b", re.IGNORECASE)

        for a in anchors:
            text = a.text_raw or a.text_norm
            if not text:
                continue
            m = en.search(text) or ru.search(text) or en_n.search(text) or ru_n.search(text)
            if not m:
                continue
            raw_num = m.group(1)
            n = _parse_int(raw_num)
            if n is None:
                # нашли маркер N, но число не распарсили — review
                return _ExtractedFactCandidate(
                    fact_type="population",
                    fact_key="planned_n_total",
                    value_json={"value": None, "unit": "participants"},
                    unit="participants",
                    status=FactStatus.NEEDS_REVIEW,
                    primary_anchor_ids=[a.anchor_id],
                    supporting_anchor_ids=[],
                    allowed_anchor_ids=allowed_anchor_ids,
                )
            return _ExtractedFactCandidate(
                fact_type="population",
                fact_key="planned_n_total",
                value_json={"value": n, "unit": "participants"},
                unit="participants",
                status=FactStatus.EXTRACTED,
                primary_anchor_ids=[a.anchor_id],
                supporting_anchor_ids=[],
                allowed_anchor_ids=allowed_anchor_ids,
            )

        return _ExtractedFactCandidate.needs_review(
            fact_type="population",
            fact_key="planned_n_total",
            extra_value_json={"unit": "participants"},
            unit="participants",
            allowed_anchor_ids=allowed_anchor_ids,
        )


@dataclass(frozen=True)
class _ExtractedFactCandidate:
    fact_type: str
    fact_key: str
    value_json: dict[str, Any]
    unit: str | None
    status: FactStatus
    primary_anchor_ids: list[str]
    supporting_anchor_ids: list[str]
    allowed_anchor_ids: set[str]

    @staticmethod
    def needs_review(
        *,
        fact_type: str,
        fact_key: str,
        allowed_anchor_ids: set[str],
        extra_value_json: dict[str, Any] | None = None,
        unit: str | None = None,
    ) -> "_ExtractedFactCandidate":
        value_json: dict[str, Any] = {"value": None}
        if extra_value_json:
            value_json.update(extra_value_json)
        return _ExtractedFactCandidate(
            fact_type=fact_type,
            fact_key=fact_key,
            value_json=value_json,
            unit=unit,
            status=FactStatus.NEEDS_REVIEW,
            primary_anchor_ids=[],
            supporting_anchor_ids=[],
            allowed_anchor_ids=allowed_anchor_ids,
        )


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _parse_int(raw: str) -> int | None:
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = cleaned.replace("\u00a0", " ")
    cleaned = re.sub(r"[ ,]", "", cleaned)
    if not cleaned.isdigit():
        return None
    try:
        val = int(cleaned)
    except ValueError:
        return None
    if val <= 0 or val > 1_000_000:
        return None
    return val


def _parse_date_to_iso(raw: str) -> str | None:
    """
    Пытаемся распарсить дату из RU/EN форматов и вернуть ISO YYYY-MM-DD.
    Поддерживаем:
    - YYYY-MM-DD
    - DD.MM.YYYY / DD/MM/YYYY
    - D Month YYYY (EN) / D <месяц> YYYY (RU)
    """
    s = (raw or "").strip()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" ,.;")

    # ISO
    m = re.match(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$", s)
    if m:
        return _iso_from_ymd(int(m.group("y")), int(m.group("m")), int(m.group("d")))

    # DD.MM.YYYY or DD/MM/YYYY
    m = re.match(r"^(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})$", s)
    if m:
        return _iso_from_ymd(int(m.group("y")), int(m.group("m")), int(m.group("d")))

    # "05 March 2021" / "5 Mar 2021" / "5 марта 2021"
    m = re.match(r"^(?P<d>\d{1,2})\s+(?P<mon>[A-Za-zА-Яа-яёЁ]+)\s+(?P<y>\d{4})$", s)
    if m:
        mon = _month_to_int(m.group("mon"))
        if mon is None:
            return None
        return _iso_from_ymd(int(m.group("y")), mon, int(m.group("d")))

    return None


def _iso_from_ymd(y: int, m: int, d: int) -> str | None:
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    return dt.isoformat()


def _month_to_int(mon: str) -> int | None:
    t = (mon or "").strip().lower()
    t = t.replace(".", "")
    ru = {
        "января": 1,
        "янв": 1,
        "февраля": 2,
        "фев": 2,
        "марта": 3,
        "мар": 3,
        "апреля": 4,
        "апр": 4,
        "мая": 5,
        "май": 5,
        "июня": 6,
        "июн": 6,
        "июля": 7,
        "июл": 7,
        "августа": 8,
        "авг": 8,
        "сентября": 9,
        "сен": 9,
        "сент": 9,
        "октября": 10,
        "окт": 10,
        "ноября": 11,
        "ноя": 11,
        "декабря": 12,
        "дек": 12,
    }
    en = {
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sep": 9,
        "sept": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
    }
    if t in ru:
        return ru[t]
    if t in en:
        return en[t]
    return None

