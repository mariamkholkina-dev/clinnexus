"""
Утилита для кластеризации заголовков из JSONL корпуса.

Использует гибридный алгоритм:
1. TF-IDF по заголовкам + агломеративная кластеризация по cosine distance
2. Опционально merge по embeddings из БД (если доступны)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_distances

# Добавляем путь к backend для импорта модулей приложения
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.config import settings
from app.services.ingestion.docx_ingestor import normalize_text
from app.services.ingestion.heading_detector import normalize_title


def load_corpus(corpus_path: Path) -> list[dict[str, Any]]:
    """Загружает корпус из JSONL файла.
    
    Args:
        corpus_path: Путь к JSONL файлу
        
    Returns:
        Список записей заголовков
    """
    records = []
    with open(corpus_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                records.append(record)
            except json.JSONDecodeError as e:
                print(f"Предупреждение: пропущена некорректная строка: {e}", file=sys.stderr)
                continue
    return records


def normalize_heading_text(text: str) -> str:
    """Нормализует текст заголовка для кластеризации.
    
    Использует существующие функции normalize_title и normalize_text.
    
    Args:
        text: Исходный текст заголовка
        
    Returns:
        Нормализованный текст
    """
    if not text:
        return ""
    # Сначала применяем normalize_title (более агрессивная нормализация для заголовков)
    normalized = normalize_title(text)
    # Затем применяем normalize_text для дополнительной нормализации
    normalized = normalize_text(normalized)
    return normalized


def compute_tfidf_vectors(headings: list[str]) -> tuple[np.ndarray, TfidfVectorizer]:
    """Вычисляет TF-IDF векторы для заголовков.
    
    Args:
        headings: Список нормализованных заголовков
        
    Returns:
        Кортеж (TF-IDF матрица, обученный vectorizer)
    """
    vectorizer = TfidfVectorizer(
        max_features=5000,
        min_df=2,  # Минимум 2 документа
        max_df=0.95,  # Максимум 95% документов
        ngram_range=(1, 2),  # Униграммы и биграммы
        lowercase=True,
        strip_accents='unicode',
    )
    tfidf_matrix = vectorizer.fit_transform(headings)
    return tfidf_matrix.toarray(), vectorizer


def cluster_by_tfidf(
    tfidf_matrix: np.ndarray,
    threshold: float,
    min_size: int,
) -> list[int]:
    """Выполняет агломеративную кластеризацию по TF-IDF векторам.
    
    Args:
        tfidf_matrix: TF-IDF матрица (n_samples, n_features)
        threshold: Порог для distance_threshold в AgglomerativeClustering
        min_size: Минимальный размер кластера
        
    Returns:
        Список меток кластеров для каждого заголовка
    """
    if len(tfidf_matrix) < 2:
        # Если заголовков меньше 2, все в одном кластере
        return [0] * len(tfidf_matrix)
    
    # Вычисляем cosine distances
    distances = cosine_distances(tfidf_matrix)
    
    # Агломеративная кластеризация с distance_threshold
    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        metric='precomputed',
        linkage='average',
    )
    labels = clustering.fit_predict(distances)
    
    # Фильтруем кластеры по минимальному размеру
    label_counts = Counter(labels)
    valid_clusters = {label for label, count in label_counts.items() if count >= min_size}
    
    # Переназначаем метки: невалидные кластеры получают -1 (шум)
    filtered_labels = []
    label_mapping: dict[int, int] = {}
    next_valid_id = 0
    
    for label in labels:
        if label in valid_clusters:
            if label not in label_mapping:
                label_mapping[label] = next_valid_id
                next_valid_id += 1
            filtered_labels.append(label_mapping[label])
        else:
            filtered_labels.append(-1)  # Шум
    
    return filtered_labels


def fetch_embeddings_for_headings(
    hdr_anchor_ids: list[str],
) -> dict[str, list[float]]:
    """Получает embeddings для заголовков из БД (если доступны).
    
    Ищет embeddings в chunks, которые содержат данные anchor_ids.
    Для каждого заголовка берёт первый найденный chunk, содержащий его anchor_id.
    
    Args:
        hdr_anchor_ids: Список UUID anchor'ов заголовков
        
    Returns:
        Словарь {hdr_anchor_id: embedding} для найденных embeddings
    """
    try:
        from sqlalchemy import create_engine, text
        
        engine = create_engine(settings.sync_database_url, echo=False)
        embeddings_map: dict[str, list[float]] = {}
        
        with engine.connect() as conn:
            # Используем ANY для каждого anchor_id
            for anchor_id in hdr_anchor_ids:
                try:
                    result = conn.execute(
                        text("""
                        SELECT c.embedding
                        FROM chunks c
                        WHERE :anchor_id = ANY(c.anchor_ids)
                        LIMIT 1
                        """),
                        {"anchor_id": anchor_id},
                    )
                    row = result.fetchone()
                    if row and row.embedding is not None:
                        # embedding может быть уже списком или строкой
                        embedding = row.embedding
                        
                        # Если это строка, парсим её
                        if isinstance(embedding, str):
                            embedding_str = embedding.strip()
                            if embedding_str.startswith('[') and embedding_str.endswith(']'):
                                embedding_str = embedding_str[1:-1]
                            embedding = [float(x.strip()) for x in embedding_str.split(',')]
                        
                        # Проверяем, что это список/массив нужной размерности
                        if isinstance(embedding, (list, tuple, np.ndarray)):
                            embedding_list = list(embedding)
                            if len(embedding_list) == 1536:  # Проверяем размерность
                                embeddings_map[anchor_id] = embedding_list
                except Exception:
                    # Пропускаем ошибки для отдельных anchor_id
                    continue
        
        engine.dispose()
        return embeddings_map
        
    except Exception as e:
        print(f"Предупреждение: не удалось загрузить embeddings из БД: {e}", file=sys.stderr)
        return {}


def merge_clusters_by_embeddings(
    clusters: list[list[int]],
    embeddings_map: dict[str, list[float]],
    hdr_anchor_ids: list[str],
    threshold: float = 0.15,
) -> list[int]:
    """Объединяет кластеры на основе embeddings.
    
    Args:
        clusters: Список списков индексов заголовков в каждом кластере
        embeddings_map: Словарь {hdr_anchor_id: embedding}
        hdr_anchor_ids: Список UUID anchor'ов заголовков
        threshold: Порог cosine distance для объединения
        
    Returns:
        Обновлённый список меток кластеров
    """
    if not embeddings_map:
        # Если нет embeddings, возвращаем исходные метки
        labels = [-1] * len(hdr_anchor_ids)
        for cluster_id, cluster_indices in enumerate(clusters):
            for idx in cluster_indices:
                labels[idx] = cluster_id
        return labels
    
    # Строим матрицу embeddings только для заголовков с доступными embeddings
    embedding_indices: dict[int, int] = {}  # {heading_idx: embedding_matrix_idx}
    embedding_vectors = []
    
    for idx, anchor_id in enumerate(hdr_anchor_ids):
        if anchor_id in embeddings_map:
            embedding_indices[idx] = len(embedding_vectors)
            embedding_vectors.append(embeddings_map[anchor_id])
    
    if len(embedding_vectors) < 2:
        # Недостаточно embeddings для merge
        labels = [-1] * len(hdr_anchor_ids)
        for cluster_id, cluster_indices in enumerate(clusters):
            for idx in cluster_indices:
                labels[idx] = cluster_id
        return labels
    
    embedding_matrix = np.array(embedding_vectors)
    
    # Вычисляем cosine distances между кластерами
    cluster_embeddings: dict[int, np.ndarray] = {}
    
    for cluster_id, cluster_indices in enumerate(clusters):
        cluster_emb_vecs = []
        for idx in cluster_indices:
            if idx in embedding_indices:
                emb_idx = embedding_indices[idx]
                cluster_emb_vecs.append(embedding_matrix[emb_idx])
        
        if cluster_emb_vecs:
            # Средний embedding кластера
            cluster_embeddings[cluster_id] = np.mean(cluster_emb_vecs, axis=0)
    
    # Объединяем близкие кластеры
    cluster_labels = list(range(len(clusters)))
    merged = set()
    
    for i, cluster_id_i in enumerate(cluster_labels):
        if cluster_id_i in merged:
            continue
        if cluster_id_i not in cluster_embeddings:
            continue
        
        for j, cluster_id_j in enumerate(cluster_labels):
            if i >= j or cluster_id_j in merged:
                continue
            if cluster_id_j not in cluster_embeddings:
                continue
            
            # Вычисляем cosine distance между кластерами
            emb_i = cluster_embeddings[cluster_id_i]
            emb_j = cluster_embeddings[cluster_id_j]
            
            norm_i = np.linalg.norm(emb_i)
            norm_j = np.linalg.norm(emb_j)
            
            if norm_i == 0 or norm_j == 0:
                continue  # Пропускаем нулевые векторы
            
            cosine_dist = 1 - np.dot(emb_i, emb_j) / (norm_i * norm_j)
            
            # Проверяем на NaN
            if np.isnan(cosine_dist):
                continue
            
            if cosine_dist < threshold:
                # Объединяем кластеры
                cluster_labels[j] = cluster_id_i
                merged.add(cluster_id_j)
    
    # Переназначаем метки
    label_mapping: dict[int, int] = {}
    next_id = 0
    
    for label in cluster_labels:
        if label not in label_mapping:
            label_mapping[label] = next_id
            next_id += 1
    
    # Формируем финальные метки
    labels = [-1] * len(hdr_anchor_ids)
    for cluster_id, cluster_indices in enumerate(clusters):
        final_label = label_mapping[cluster_labels[cluster_id]]
        for idx in cluster_indices:
            labels[idx] = final_label
    
    return labels


def build_clusters(
    records: list[dict[str, Any]],
    labels: list[int],
) -> list[dict[str, Any]]:
    """Строит структуру кластеров из записей и меток.
    
    Args:
        records: Список записей заголовков
        labels: Список меток кластеров
        
    Returns:
        Список кластеров с полной информацией
    """
    # Группируем записи по кластерам
    clusters_dict: dict[int, list[dict[str, Any]]] = defaultdict(list)
    
    for record, label in zip(records, labels):
        if label >= 0:  # Игнорируем шум (label == -1)
            clusters_dict[label].append(record)
    
    # Формируем выходные кластеры
    clusters = []
    for cluster_id, cluster_records in sorted(clusters_dict.items()):
        # Разделяем по языкам
        titles_ru: list[str] = []
        titles_en: list[str] = []
        
        heading_levels: list[int] = []
        content_type_counts: Counter[str] = Counter()
        total_chars_list: list[int] = []
        
        examples: list[dict[str, Any]] = []
        
        for record in cluster_records:
            heading_text_raw = record.get("heading_text_raw", "")
            detected_language = record.get("detected_language", "unknown")
            
            # Собираем топ заголовков по языкам
            if detected_language in ("ru", "mixed"):
                if heading_text_raw and heading_text_raw not in titles_ru:
                    titles_ru.append(heading_text_raw)
            if detected_language in ("en", "mixed"):
                if heading_text_raw and heading_text_raw not in titles_en:
                    titles_en.append(heading_text_raw)
            
            # Статистика
            heading_level = record.get("heading_level")
            if heading_level is not None:
                heading_levels.append(heading_level)
            
            window = record.get("window", {})
            content_type_counts.update(window.get("content_type_counts", {}))
            total_chars = window.get("total_chars", 0)
            if total_chars > 0:
                total_chars_list.append(total_chars)
            
            # Примеры (до 10)
            if len(examples) < 10:
                examples.append({
                    "doc_version_id": record.get("doc_version_id"),
                    "section_path": record.get("section_path"),
                    "heading_text_raw": heading_text_raw,
                })
        
        # Топ-20 заголовков
        top_titles_ru = titles_ru[:20]
        top_titles_en = titles_en[:20]
        
        # Гистограмма уровней заголовков
        heading_level_histogram = dict(Counter(heading_levels))
        
        # Распределение content_type
        content_type_distribution = dict(content_type_counts)
        
        # Среднее total_chars
        avg_total_chars = float(np.mean(total_chars_list)) if total_chars_list else 0.0
        
        cluster = {
            "cluster_id": cluster_id,
            "top_titles_ru": top_titles_ru,
            "top_titles_en": top_titles_en,
            "examples": examples,
            "stats": {
                "heading_level_histogram": heading_level_histogram,
                "content_type_distribution": content_type_distribution,
                "avg_total_chars": round(avg_total_chars, 2),
            },
        }
        clusters.append(cluster)
    
    return clusters


def main() -> None:
    """Главная функция CLI."""
    parser = argparse.ArgumentParser(
        description="Кластеризация заголовков из JSONL корпуса"
    )
    parser.add_argument(
        "--in",
        dest="input_path",
        type=str,
        required=True,
        help="Путь к входному JSONL файлу (corpus)",
    )
    parser.add_argument(
        "--out",
        dest="output_path",
        type=str,
        required=True,
        help="Путь к выходному JSON файлу (clusters.json)",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=3,
        help="Минимальный размер кластера (по умолчанию: 3)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.22,
        help="Порог distance для кластеризации (по умолчанию: 0.22)",
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input_path)
    output_path = Path(args.output_path)
    
    if not input_path.exists():
        print(f"Ошибка: файл {input_path} не найден", file=sys.stderr)
        sys.exit(1)
    
    # Загружаем корпус
    print(f"Загрузка корпуса из {input_path}...", file=sys.stderr)
    records = load_corpus(input_path)
    print(f"Загружено {len(records)} записей", file=sys.stderr)
    
    if len(records) == 0:
        print("Ошибка: корпус пуст", file=sys.stderr)
        sys.exit(1)
    
    # Нормализуем заголовки
    print("Нормализация заголовков...", file=sys.stderr)
    headings = []
    hdr_anchor_ids = []
    
    for record in records:
        heading_text_norm = record.get("heading_text_norm", "")
        if not heading_text_norm:
            heading_text_raw = record.get("heading_text_raw", "")
            heading_text_norm = normalize_heading_text(heading_text_raw)
        
        headings.append(heading_text_norm)
        hdr_anchor_ids.append(record.get("hdr_anchor_id", ""))
    
    # TF-IDF векторизация
    print("Вычисление TF-IDF векторов...", file=sys.stderr)
    tfidf_matrix, vectorizer = compute_tfidf_vectors(headings)
    print(f"Размерность TF-IDF: {tfidf_matrix.shape}", file=sys.stderr)
    
    # Кластеризация по TF-IDF
    print(f"Кластеризация (threshold={args.threshold}, min_size={args.min_size})...", file=sys.stderr)
    labels = cluster_by_tfidf(tfidf_matrix, args.threshold, args.min_size)
    
    # Группируем по кластерам для merge по embeddings
    clusters_dict: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        if label >= 0:
            clusters_dict[label].append(idx)
    
    clusters_list = [indices for indices in clusters_dict.values()]
    
    # Опциональный merge по embeddings
    print("Попытка загрузки embeddings из БД...", file=sys.stderr)
    embeddings_map = fetch_embeddings_for_headings(hdr_anchor_ids)
    
    if embeddings_map:
        print(f"Загружено {len(embeddings_map)} embeddings, выполняется merge...", file=sys.stderr)
        labels = merge_clusters_by_embeddings(
            clusters_list,
            embeddings_map,
            hdr_anchor_ids,
            threshold=args.threshold * 0.7,  # Более строгий порог для embeddings
        )
    else:
        print("Embeddings не найдены, используется только TF-IDF кластеризация", file=sys.stderr)
    
    # Строим финальные кластеры
    print("Формирование выходных кластеров...", file=sys.stderr)
    clusters = build_clusters(records, labels)
    print(f"Создано {len(clusters)} кластеров", file=sys.stderr)
    
    # Сохраняем результат
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(clusters, f, ensure_ascii=False, indent=2)
    
    print(f"Результат сохранён в {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

