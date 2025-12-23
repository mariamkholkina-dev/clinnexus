-- Минимальный дамп данных для проверки корректности ключей и метрик
-- Экспорт без PHI (персональных данных)

-- 1. Примеры document_versions (id, doc_type, language, last_ingestion_run_id)
SELECT 
    id,
    document_id,
    doc_type,
    document_language,
    last_ingestion_run_id,
    ingestion_status,
    created_at
FROM document_versions
LIMIT 2;

-- 2. Примеры ingestion_runs.summary_json и quality_json
SELECT 
    id,
    doc_version_id,
    status,
    pipeline_version,
    pipeline_config_hash,
    summary_json,
    quality_json,
    warnings_json,
    errors_json,
    duration_ms,
    created_at,
    finished_at
FROM ingestion_runs
WHERE summary_json IS NOT NULL
LIMIT 2;

-- 3. Агрегаты по anchors/chunks для 1 документа
-- Выбираем первый документ с ingestion
WITH sample_doc AS (
    SELECT doc_version_id 
    FROM ingestion_runs 
    WHERE summary_json IS NOT NULL 
    LIMIT 1
)
SELECT 
    'anchors' as entity_type,
    content_type,
    source_zone,
    language,
    COUNT(*) as count
FROM anchors
WHERE doc_version_id = (SELECT doc_version_id FROM sample_doc)
GROUP BY content_type, source_zone, language
UNION ALL
SELECT 
    'chunks' as entity_type,
    NULL::text as content_type,
    source_zone,
    language,
    COUNT(*) as count
FROM chunks
WHERE doc_version_id = (SELECT doc_version_id FROM sample_doc)
GROUP BY source_zone, language
ORDER BY entity_type, content_type, source_zone, language;

-- 4. Примеры cluster_assignments
SELECT 
    id,
    doc_version_id,
    cluster_id,
    topic_key,
    mapped_by,
    confidence,
    notes,
    mapping_debug_json,
    created_at
FROM cluster_assignments
LIMIT 5;

-- 5. Примеры topic_evidence
SELECT 
    id,
    doc_version_id,
    topic_key,
    source_zone,
    language,
    array_length(anchor_ids, 1) as anchor_ids_count,
    array_length(chunk_ids, 1) as chunk_ids_count,
    score,
    evidence_json,
    created_at
FROM topic_evidence
LIMIT 5;

-- 6. Примеры heading_clusters (для контекста)
SELECT 
    id,
    doc_version_id,
    cluster_id,
    language,
    jsonb_array_length(top_titles_json) as top_titles_count,
    jsonb_array_length(examples_json) as examples_count,
    stats_json,
    created_at
FROM heading_clusters
LIMIT 3;

