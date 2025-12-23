-- SQL запрос для получения списка файлов без SOA из последней ingestion campaign
-- 
-- Находит последнюю кампанию по максимальному started_at в ingestion_runs,
-- затем выводит названия файлов из версий документов, где SOA не найден

WITH last_campaign_window AS (
    -- Определяем временное окно последней кампании
    -- Берем максимальный started_at и все запуски в пределах 2 часов от него
    SELECT 
        MAX(started_at) AS campaign_start,
        MAX(started_at) - INTERVAL '2 hours' AS window_start,
        MAX(started_at) + INTERVAL '1 hour' AS window_end
    FROM ingestion_runs
),
campaign_versions AS (
    -- Получаем все версии документов из последней кампании
    SELECT DISTINCT ir.doc_version_id
    FROM ingestion_runs ir
    CROSS JOIN last_campaign_window lcw
    WHERE ir.started_at >= lcw.window_start
        AND ir.started_at <= lcw.window_end
)
SELECT 
    -- Извлекаем имя файла из source_file_uri
    -- Обрабатываем пути с прямыми и обратными слэшами (Windows/Linux)
    COALESCE(
        NULLIF(
            reverse(split_part(reverse(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(dv.source_file_uri, '^file:///', ''),
                        '^file://', ''
                    ),
                    '\\', '/'  -- Заменяем обратные слэши на прямые для единообразия
                )
            ), '/', 1)),
            ''
        ),
        dv.source_file_uri
    ) AS filename,
    dv.id AS version_id,
    d.title AS document_title,
    dv.version_label,
    dv.ingestion_status,
    dv.created_at
FROM document_versions dv
INNER JOIN campaign_versions cv ON dv.id = cv.doc_version_id
INNER JOIN documents d ON dv.document_id = d.id
WHERE 
    -- Проверяем, что SOA не найден
    -- COALESCE возвращает false, если значение null или отсутствует
    COALESCE(
        (dv.ingestion_summary_json->'metrics'->'soa'->>'found')::boolean,
        false
    ) = false
    AND dv.source_file_uri IS NOT NULL
ORDER BY dv.created_at DESC;

-- ============================================================================
-- АЛЬТЕРНАТИВНЫЙ ВАРИАНТ (если таблица ingestion_runs не используется):
-- Использует последние N часов по created_at в document_versions
-- ============================================================================
/*
WITH recent_versions AS (
    -- Берем версии, созданные за последние 24 часа
    SELECT id
    FROM document_versions
    WHERE created_at >= NOW() - INTERVAL '24 hours'
        AND ingestion_status IN ('ready', 'needs_review')
)
SELECT 
    COALESCE(
        NULLIF(
            reverse(split_part(reverse(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(dv.source_file_uri, '^file:///', ''),
                        '^file://', ''
                    ),
                    '\\', '/'
                )
            ), '/', 1)),
            ''
        ),
        dv.source_file_uri
    ) AS filename,
    dv.id AS version_id,
    d.title AS document_title,
    dv.version_label,
    dv.ingestion_status,
    dv.created_at
FROM document_versions dv
INNER JOIN recent_versions rv ON dv.id = rv.id
INNER JOIN documents d ON dv.document_id = d.id
WHERE 
    COALESCE(
        (dv.ingestion_summary_json->'metrics'->'soa'->>'found')::boolean,
        false
    ) = false
    AND dv.source_file_uri IS NOT NULL
ORDER BY dv.created_at DESC;
*/

