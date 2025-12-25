"""Сервис для проверки согласованности извлеченных фактов исследования.

Проверяет факты на логические противоречия и конфликты:
- Структурные проверки (alternatives, range checks, power/alpha)
- Кросс-документный контроль через AnchorMatch
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import ConflictSeverity, ConflictStatus, FactStatus, TaskStatus, TaskType
from app.db.models.anchor_matches import AnchorMatch
from app.db.models.change import Task
from app.db.models.conflicts import Conflict, ConflictItem
from app.db.models.facts import Fact, FactEvidence


class FactConsistencyService:
    """Сервис для проверки согласованности фактов исследования."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def check_study_consistency(self, study_id: UUID) -> list[Conflict]:
        """
        Проверяет все активные факты исследования на логические противоречия и конфликты.

        Args:
            study_id: ID исследования для проверки

        Returns:
            Список обнаруженных Conflict объектов
        """
        logger.info(f"Начало проверки согласованности фактов для study_id={study_id}")

        # Загружаем все активные факты (исключаем уже помеченные как conflicting)
        stmt = select(Fact).where(
            Fact.study_id == study_id,
            Fact.status != FactStatus.CONFLICTING,
        )
        result = await self.db.execute(stmt)
        facts = result.scalars().all()

        if not facts:
            logger.info(f"Нет активных фактов для проверки в study_id={study_id}")
            return []

        logger.info(f"Загружено {len(facts)} активных фактов для проверки")

        conflicts: list[Conflict] = []
        conflict_items: list[ConflictItem] = []

        # 1. Структурные проверки
        structural_data = await self._check_structural_consistency(facts, study_id)
        conflicts.extend(structural_data["conflicts"])
        conflict_items.extend(structural_data["items"])

        # 2. Кросс-документный контроль
        cross_doc_data = await self._check_cross_document_consistency(facts, study_id)
        conflicts.extend(cross_doc_data["conflicts"])
        conflict_items.extend(cross_doc_data["items"])

        # Сохраняем все конфликты в БД
        for conflict in conflicts:
            self.db.add(conflict)
        await self.db.flush()  # Получаем ID для конфликтов

        # Теперь создаём ConflictItem с правильными conflict_id
        for item_data in conflict_items:
            # Используем индекс для получения правильного conflict_id после flush
            conflict_index = item_data.get("conflict_index")
            if conflict_index is not None and conflict_index < len(conflicts):
                actual_conflict_id = conflicts[conflict_index].id
            else:
                # Fallback: ищем конфликт по другим признакам (не должно произойти)
                continue

            conflict_item = ConflictItem(
                conflict_id=actual_conflict_id,
                left_anchor_id=item_data.get("left_anchor_id"),
                right_anchor_id=item_data.get("right_anchor_id"),
                left_fact_id=item_data.get("left_fact_id"),
                right_fact_id=item_data.get("right_fact_id"),
                evidence_json=item_data.get("evidence_json"),
            )
            self.db.add(conflict_item)

        await self.db.commit()

        # Создаём задачи для критических конфликтов
        critical_conflicts = [c for c in conflicts if c.severity == ConflictSeverity.CRITICAL]
        if critical_conflicts:
            await self._create_resolve_conflict_tasks(study_id, critical_conflicts)

        logger.info(
            f"Проверка завершена: найдено {len(conflicts)} конфликтов "
            f"(критических: {len(critical_conflicts)})"
        )

        return conflicts

    async def _check_structural_consistency(
        self, facts: list[Fact], study_id: UUID
    ) -> dict[str, list]:
        """Выполняет структурные проверки фактов.
        
        Returns:
            Словарь с ключами 'conflicts' (список Conflict) и 'items' (список данных для ConflictItem)
        """
        conflicts: list[Conflict] = []
        conflict_items_data: list[dict[str, Any]] = []

        for fact in facts:
            # 1. Conflict Detection: проверка alternatives в meta_json
            if fact.meta_json and "alternatives" in fact.meta_json:
                alternatives = fact.meta_json.get("alternatives", [])
                if alternatives:
                    main_value = self._extract_main_value(fact.value_json)
                    has_conflict = False
                    for alt in alternatives:
                        alt_value = self._extract_main_value(alt.get("value", alt)) if isinstance(alt, dict) else alt
                        # Используем нормализацию для сравнения: одинаковые значения не должны создавать конфликт
                        normalized_main = self._normalize_value(main_value)
                        normalized_alt = self._normalize_value(alt_value)
                        if normalized_main != normalized_alt:
                            has_conflict = True
                            break

                    if has_conflict:
                        # Помечаем факт как conflicting
                        fact.status = FactStatus.CONFLICTING
                        await self.db.flush()

                        # Создаём конфликт
                        conflict = Conflict(
                            study_id=study_id,
                            conflict_type="structural_alternatives",
                            severity=ConflictSeverity.MEDIUM,
                            status=ConflictStatus.OPEN,
                            title=f"Конфликт альтернативных значений для факта {fact.fact_key}",
                            description=(
                                f"Факт {fact.fact_type}:{fact.fact_key} имеет альтернативные значения "
                                f"в meta_json['alternatives'], отличные от основного значения. "
                                f"Основное: {main_value}, Альтернативы: {alternatives}"
                            ),
                            owner_user_id=None,
                        )
                        conflicts.append(conflict)

                        # Сохраняем данные для ConflictItem (создадим после сохранения конфликта)
                        conflict_items_data.append({
                            "conflict_id": conflict.id,  # Временно, будет заменено после flush
                            "conflict_index": len(conflicts) - 1,
                            "left_fact_id": fact.id,
                            "right_fact_id": None,
                            "evidence_json": {
                                "main_value": main_value,
                                "alternatives": alternatives,
                            },
                        })

            # 2. Range Check: проверка age_min <= age_max
            if fact.fact_key in ("age_min", "age_max", "age_range"):
                age_min = None
                age_max = None

                if fact.fact_key == "age_min":
                    age_min = self._extract_numeric_value(fact.value_json)
                elif fact.fact_key == "age_max":
                    age_max = self._extract_numeric_value(fact.value_json)
                elif fact.fact_key == "age_range":
                    value = fact.value_json
                    if isinstance(value, dict):
                        age_min = self._extract_numeric_value(value.get("min"))
                        age_max = self._extract_numeric_value(value.get("max"))

                # Ищем соответствующий факт для сравнения
                if age_min is not None:
                    stmt = select(Fact).where(
                        Fact.study_id == study_id,
                        Fact.fact_key == "age_max",
                        Fact.id != fact.id,
                    )
                    result = await self.db.execute(stmt)
                    age_max_fact = result.scalar_one_or_none()
                    if age_max_fact:
                        age_max = self._extract_numeric_value(age_max_fact.value_json)

                if age_max is not None:
                    stmt = select(Fact).where(
                        Fact.study_id == study_id,
                        Fact.fact_key == "age_min",
                        Fact.id != fact.id,
                    )
                    result = await self.db.execute(stmt)
                    age_min_fact = result.scalar_one_or_none()
                    if age_min_fact:
                        age_min = self._extract_numeric_value(age_min_fact.value_json)

                if age_min is not None and age_max is not None:
                    if age_min > age_max:
                        conflict = Conflict(
                            study_id=study_id,
                            conflict_type="structural_range",
                            severity=ConflictSeverity.HIGH,
                            status=ConflictStatus.OPEN,
                            title="Некорректный диапазон возраста",
                            description=(
                                f"age_min ({age_min}) больше age_max ({age_max}). "
                                f"Минимальный возраст должен быть меньше или равен максимальному."
                            ),
                            owner_user_id=None,
                        )
                        conflicts.append(conflict)

                        # Сохраняем данные для ConflictItem
                        left_fact_id = fact.id if fact.fact_key in ("age_min", "age_range") else None
                        right_fact_id = (
                            age_max_fact.id if age_max_fact and fact.fact_key == "age_min" else fact.id
                        )
                        conflict_items_data.append({
                            "conflict_id": conflict.id,  # Временно
                            "conflict_index": len(conflicts) - 1,
                            "left_fact_id": left_fact_id,
                            "right_fact_id": right_fact_id,
                            "evidence_json": {"age_min": age_min, "age_max": age_max},
                        })

            # 3. Power/Alpha Check
            if fact.fact_key == "alpha":
                alpha = self._extract_numeric_value(fact.value_json)
                if alpha is not None and alpha >= 0.1:
                    conflict = Conflict(
                        study_id=study_id,
                        conflict_type="structural_alpha",
                        severity=ConflictSeverity.MEDIUM,
                        status=ConflictStatus.OPEN,
                        title="Некорректное значение alpha",
                        description=(
                            f"Значение alpha ({alpha}) должно быть меньше 0.1. "
                            f"Текущее значение не соответствует стандартным границам."
                        ),
                        owner_user_id=None,
                    )
                    conflicts.append(conflict)

                    conflict_items_data.append({
                        "conflict_id": conflict.id,  # Временно
                        "conflict_index": len(conflicts) - 1,
                        "left_fact_id": fact.id,
                        "right_fact_id": None,
                        "evidence_json": {"alpha": alpha, "threshold": 0.1},
                    })

            if fact.fact_key == "power":
                power = self._extract_numeric_value(fact.value_json)
                if power is not None and power <= 0.7:
                    conflict = Conflict(
                        study_id=study_id,
                        conflict_type="structural_power",
                        severity=ConflictSeverity.MEDIUM,
                        status=ConflictStatus.OPEN,
                        title="Некорректное значение power",
                        description=(
                            f"Значение power ({power}) должно быть больше 0.7. "
                            f"Текущее значение не соответствует стандартным границам."
                        ),
                        owner_user_id=None,
                    )
                    conflicts.append(conflict)

                    conflict_items_data.append({
                        "conflict_id": conflict.id,  # Временно
                        "conflict_index": len(conflicts) - 1,
                        "left_fact_id": fact.id,
                        "right_fact_id": None,
                        "evidence_json": {"power": power, "threshold": 0.7},
                    })

        return {"conflicts": conflicts, "items": conflict_items_data}

    async def _check_cross_document_consistency(
        self, facts: list[Fact], study_id: UUID
    ) -> dict[str, list]:
        """Проверяет согласованность фактов между разными версиями документов через AnchorMatch.
        
        Returns:
            Словарь с ключами 'conflicts' (список Conflict) и 'items' (список данных для ConflictItem)
        """
        conflicts: list[Conflict] = []
        conflict_items_data: list[dict[str, Any]] = []

        # Группируем факты по fact_key
        facts_by_key: dict[str, list[Fact]] = {}
        for fact in facts:
            if fact.fact_key not in facts_by_key:
                facts_by_key[fact.fact_key] = []
            facts_by_key[fact.fact_key].append(fact)

        # Для каждого fact_key проверяем факты из разных версий
        for fact_key, fact_list in facts_by_key.items():
            if len(fact_list) < 2:
                continue  # Нужно минимум 2 факта для сравнения

            # Группируем по версиям документов
            facts_by_version: dict[UUID | None, list[Fact]] = {}
            for fact in fact_list:
                version_id = fact.created_from_doc_version_id
                if version_id not in facts_by_version:
                    facts_by_version[version_id] = []
                facts_by_version[version_id].append(fact)
            
            # Внутренний арбитраж: если для одного fact_key есть несколько фактов
            # с одинаковым doc_version_id, это шум извлечения - разрешаем в пользу максимального confidence
            for version_id, version_facts in facts_by_version.items():
                if version_id is not None and len(version_facts) > 1:
                    # Это конфликт внутри одного документа - разрешаем автоматически
                    # Выбираем факт с максимальным confidence
                    best_fact = max(version_facts, key=lambda f: f.confidence or 0.0)
                    logger.info(
                        f"Внутренний арбитраж для факта {fact_key} в документе {version_id}: "
                        f"выбран факт с confidence={best_fact.confidence} из {len(version_facts)} дубликатов"
                    )
                    
                    # Обновляем остальные факты, чтобы они ссылались на лучший
                    # (на самом деле, факты уникальны по (study_id, fact_type, fact_key),
                    # так что это не должно происходить, но на всякий случай логируем)
                    # Если же это возможно через какую-то гонку условий, просто пропускаем эти факты
                    # и оставляем только лучший для дальнейшего сравнения
                    facts_by_version[version_id] = [best_fact]

            # Сравниваем факты между версиями (только между РАЗНЫМИ версиями)
            version_ids = list(facts_by_version.keys())
            for i, version_id_a in enumerate(version_ids):
                if version_id_a is None:
                    continue
                for version_id_b in version_ids[i + 1 :]:
                    if version_id_b is None:
                        continue
                    
                    # Внутренний арбитраж: если версии совпадают, это не конфликт документов,
                    # а шум извлечения - пропускаем (это не должно происходить из-за группировки,
                    # но на всякий случай проверяем)
                    if version_id_a == version_id_b:
                        logger.debug(
                            f"Пропуск сравнения фактов из одной версии документа {version_id_a} "
                            f"для fact_key {fact_key} - это не конфликт документов"
                        )
                        continue

                    # Проверяем, есть ли AnchorMatch между этими версиями
                    stmt = select(AnchorMatch).where(
                        AnchorMatch.from_doc_version_id == version_id_a,
                        AnchorMatch.to_doc_version_id == version_id_b,
                    )
                    result = await self.db.execute(stmt)
                    matches = result.scalars().all()

                    if not matches:
                        # Нет матчей между версиями, пропускаем
                        continue

                    # Получаем факты для обеих версий
                    facts_a = facts_by_version[version_id_a]
                    facts_b = facts_by_version[version_id_b]

                    # Сравниваем значения фактов
                    for fact_a in facts_a:
                        for fact_b in facts_b:
                            # Внутренний арбитраж: если факты из одной версии документа,
                            # это не конфликт документов, а шум извлечения - пропускаем конфликт
                            if fact_a.created_from_doc_version_id == fact_b.created_from_doc_version_id:
                                if fact_a.created_from_doc_version_id is not None:
                                    # Выбираем факт с максимальным confidence для логирования
                                    best_conf = max(
                                        fact_a.confidence or 0.0,
                                        fact_b.confidence or 0.0
                                    )
                                    logger.debug(
                                        f"Внутренний арбитраж для fact_key {fact_key}: "
                                        f"факты из одной версии {fact_a.created_from_doc_version_id}, "
                                        f"пропуск конфликта (лучший confidence={best_conf:.2f})"
                                    )
                                    # Пропускаем создание конфликта для фактов из одной версии
                                    continue
                            
                            value_a = self._normalize_value(fact_a.value_json)
                            value_b = self._normalize_value(fact_b.value_json)

                            if value_a != value_b:
                                # Значения различаются - создаём конфликт
                                # Проверяем, связаны ли факты через AnchorMatch
                                if await self._are_facts_related_via_anchors(fact_a, fact_b, matches):
                                    # Определяем критичность: конфликты по sample_size/N считаются критическими
                                    severity = (
                                        ConflictSeverity.CRITICAL
                                        if fact_key in ("sample_size", "planned_n_total", "planned_n_per_arm", "N")
                                        else ConflictSeverity.HIGH
                                    )
                                    
                                    conflict = Conflict(
                                        study_id=study_id,
                                        conflict_type="cross_document_value_change",
                                        severity=severity,
                                        status=ConflictStatus.OPEN,
                                        title=f"Изменение значения факта {fact_key} между версиями",
                                        description=(
                                            f"Значение факта {fact_key} изменилось между версиями документов. "
                                            f"Версия {version_id_a}: {value_a}, "
                                            f"Версия {version_id_b}: {value_b}"
                                        ),
                                        owner_user_id=None,
                                    )
                                    conflicts.append(conflict)

                                    # Получаем anchor_ids для фактов
                                    anchor_ids_a = await self._get_fact_anchor_ids(fact_a.id)
                                    anchor_ids_b = await self._get_fact_anchor_ids(fact_b.id)

                                    # Находим соответствующие anchor_id через AnchorMatch
                                    left_anchor_id = None
                                    right_anchor_id = None
                                    for match in matches:
                                        if match.from_anchor_id in anchor_ids_a:
                                            left_anchor_id = match.from_anchor_id
                                            right_anchor_id = match.to_anchor_id
                                            break

                                    conflict_items_data.append({
                                        "conflict_id": conflict.id,  # Временно
                                        "conflict_index": len(conflicts) - 1,
                                        "left_anchor_id": left_anchor_id,
                                        "right_anchor_id": right_anchor_id,
                                        "left_fact_id": fact_a.id,
                                        "right_fact_id": fact_b.id,
                                        "evidence_json": {
                                            "value_a": value_a,
                                            "value_b": value_b,
                                            "version_a": str(version_id_a),
                                            "version_b": str(version_id_b),
                                        },
                                    })

        return {"conflicts": conflicts, "items": conflict_items_data}

    async def _are_facts_related_via_anchors(
        self, fact_a: Fact, fact_b: Fact, matches: list[AnchorMatch]
    ) -> bool:
        """Проверяет, связаны ли факты через AnchorMatch."""
        anchor_ids_a = await self._get_fact_anchor_ids(fact_a.id)
        anchor_ids_b = await self._get_fact_anchor_ids(fact_b.id)

        # Создаём множества anchor_id для быстрого поиска
        anchor_ids_a_set = set(anchor_ids_a)
        anchor_ids_b_set = set(anchor_ids_b)

        # Проверяем, есть ли матч между anchor_id фактов
        for match in matches:
            if match.from_anchor_id in anchor_ids_a_set and match.to_anchor_id in anchor_ids_b_set:
                return True

        return False

    async def _get_fact_anchor_ids(self, fact_id: UUID) -> list[str]:
        """Получает список anchor_id для факта."""
        stmt = select(FactEvidence).where(FactEvidence.fact_id == fact_id)
        result = await self.db.execute(stmt)
        evidences = result.scalars().all()
        return [ev.anchor_id for ev in evidences]

    async def _create_resolve_conflict_tasks(
        self, study_id: UUID, critical_conflicts: list[Conflict]
    ) -> None:
        """Создаёт системные задачи для разрешения критических конфликтов."""
        # Загружаем все существующие задачи для этого исследования
        stmt = select(Task).where(
            Task.study_id == study_id,
            Task.type == TaskType.RESOLVE_CONFLICT,
            Task.status != TaskStatus.DONE,
            Task.status != TaskStatus.CANCELLED,
        )
        result = await self.db.execute(stmt)
        existing_tasks = result.scalars().all()

        # Создаём множество ID конфликтов, для которых уже есть задачи
        existing_conflict_ids = {
            task.payload_json.get("conflict_id")
            for task in existing_tasks
            if task.payload_json and "conflict_id" in task.payload_json
        }

        for conflict in critical_conflicts:
            conflict_id_str = str(conflict.id)
            if conflict_id_str in existing_conflict_ids:
                logger.info(
                    f"Задача для конфликта {conflict.id} уже существует"
                )
                continue

            # Создаём новую задачу
            task = Task(
                study_id=study_id,
                type=TaskType.RESOLVE_CONFLICT,
                status=TaskStatus.OPEN,
                assigned_to=None,
                payload_json={
                    "conflict_id": conflict_id_str,
                    "conflict_type": conflict.conflict_type,
                    "severity": conflict.severity.value,
                    "title": conflict.title,
                },
            )
            self.db.add(task)
            logger.info(f"Создана задача resolve_conflict для конфликта {conflict.id}")

        await self.db.commit()

    def _extract_main_value(self, value: Any) -> Any:
        """Извлекает основное значение из value_json."""
        if isinstance(value, dict):
            return value.get("value", value)
        return value

    def _extract_numeric_value(self, value: Any) -> float | int | None:
        """Извлекает числовое значение из value_json."""
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, dict):
            val = value.get("value", value)
            if isinstance(val, (int, float)):
                return val
        if isinstance(value, str):
            try:
                # Пытаемся распарсить число из строки
                if "." in value:
                    return float(value)
                return int(value)
            except (ValueError, TypeError):
                pass
        return None

    def _normalize_value(self, value: Any) -> str:
        """Нормализует значение для сравнения.
        
        Правила нормализации:
        - Числа и строки с числами приводятся к числу, затем к строке ('64' и 64 -> '64')
        - Даты приводятся к ISO формату (12.04.2010 и 2010-04-12 -> '2010-04-12')
        - Строки приводятся к нижнему регистру и обрезаются
        """
        if isinstance(value, dict):
            val = value.get("value", value)
        else:
            val = value

        # Нормализация чисел: строки с числами и числа приводятся к одному виду
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, str):
            # Пробуем распарсить как число
            cleaned = val.strip().replace("\u00a0", " ").replace(" ", "").replace(",", "")
            if cleaned.isdigit():
                return str(int(cleaned))
            # Пробуем распарсить как float
            try:
                cleaned_float = val.strip().replace("\u00a0", " ").replace(",", ".")
                float_val = float(cleaned_float)
                # Проверяем, что это действительно число, а не просто строка с точкой
                if cleaned_float.replace(".", "").replace("-", "").isdigit():
                    return str(float_val)
            except (ValueError, AttributeError):
                pass
            
            # Нормализация дат: приводим к ISO формату
            normalized_date = self._normalize_date_to_iso(val)
            if normalized_date:
                return normalized_date
            
            # Обычная нормализация строк
            return val.lower().strip()
        
        if isinstance(val, list):
            return json.dumps(sorted(str(item) for item in val), sort_keys=True)
        return json.dumps(val, sort_keys=True) if val is not None else ""
    
    def _normalize_date_to_iso(self, date_str: str) -> str | None:
        """Нормализует дату к ISO формату YYYY-MM-DD.
        
        Поддерживает форматы:
        - DD.MM.YYYY -> YYYY-MM-DD
        - DD/MM/YYYY -> YYYY-MM-DD
        - YYYY-MM-DD -> YYYY-MM-DD (уже ISO)
        - "12 апреля 2010" -> YYYY-MM-DD
        """
        if not date_str or not isinstance(date_str, str):
            return None
        
        import re
        from datetime import date
        
        s = date_str.strip()
        if not s:
            return None
        
        # ISO формат (уже нормализован)
        m = re.match(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$", s)
        if m:
            try:
                dt = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
                return dt.isoformat()
            except ValueError:
                pass
        
        # DD.MM.YYYY or DD/MM/YYYY
        m = re.match(r"^(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})$", s)
        if m:
            try:
                dt = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
                return dt.isoformat()
            except ValueError:
                pass
        
        # "12 апреля 2010" / "12 April 2010"
        m = re.search(r"(?P<d>\d{1,2})\s+(?P<mon>[A-Za-zА-Яа-яёЁ]+)\s+(?P<y>\d{4})", s)
        if m:
            mon = self._month_to_int(m.group("mon"))
            if mon is not None:
                try:
                    dt = date(int(m.group("y")), mon, int(m.group("d")))
                    return dt.isoformat()
                except ValueError:
                    pass
        
        return None
    
    def _month_to_int(self, mon: str) -> int | None:
        """Преобразует название месяца в число."""
        t = (mon or "").strip().lower().replace(".", "")
        ru = {
            "января": 1, "янв": 1, "февраля": 2, "фев": 2, "марта": 3, "мар": 3,
            "апреля": 4, "апр": 4, "мая": 5, "май": 5, "июня": 6, "июн": 6,
            "июля": 7, "июл": 7, "августа": 8, "авг": 8, "сентября": 9, "сен": 9, "сент": 9,
            "октября": 10, "окт": 10, "ноября": 11, "ноя": 11, "декабря": 12, "дек": 12,
        }
        en = {
            "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
            "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
            "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
            "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
        }
        return ru.get(t) or en.get(t)

