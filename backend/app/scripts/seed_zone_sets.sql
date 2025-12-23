-- Seed данные для zone_sets и zone_crosswalk
-- Используется для начального заполнения таблиц после миграции

-- ============================================================================
-- Zone Sets: 12 зон для protocol
-- ============================================================================

INSERT INTO zone_sets (doc_type, zone_key, is_active, created_at)
VALUES
    ('protocol', 'overview', true, NOW()),
    ('protocol', 'design', true, NOW()),
    ('protocol', 'ip', true, NOW()),
    ('protocol', 'statistics', true, NOW()),
    ('protocol', 'safety', true, NOW()),
    ('protocol', 'endpoints', true, NOW()),
    ('protocol', 'population', true, NOW()),
    ('protocol', 'procedures', true, NOW()),
    ('protocol', 'data_management', true, NOW()),
    ('protocol', 'ethics', true, NOW()),
    ('protocol', 'admin', true, NOW()),
    ('protocol', 'appendix', true, NOW())
ON CONFLICT (doc_type, zone_key) 
DO UPDATE SET is_active = EXCLUDED.is_active;

-- ============================================================================
-- Zone Crosswalk: минимальные маппинги protocol -> csr
-- ============================================================================

INSERT INTO zone_crosswalk (
    from_doc_type, 
    from_zone_key, 
    to_doc_type, 
    to_zone_key, 
    weight, 
    notes, 
    is_active, 
    created_at
)
VALUES
    -- protocol.statistics -> csr.statistics_results (1.0) + csr.tfl (0.8)
    ('protocol', 'statistics', 'csr', 'statistics_results', 1.0, 
     'Прямой маппинг статистики протокола в результаты CSR', true, NOW()),
    ('protocol', 'statistics', 'csr', 'tfl', 0.8, 
     'Статистика протокола также релевантна для TFL', true, NOW()),
    
    -- protocol.safety -> csr.safety_results (1.0) + csr.tfl (0.8)
    ('protocol', 'safety', 'csr', 'safety_results', 1.0, 
     'Прямой маппинг безопасности протокола в результаты CSR', true, NOW()),
    ('protocol', 'safety', 'csr', 'tfl', 0.8, 
     'Безопасность протокола также релевантна для TFL', true, NOW()),
    
    -- protocol.population -> csr.population (1.0) + csr.disposition (0.8)
    ('protocol', 'population', 'csr', 'population', 1.0, 
     'Прямой маппинг популяции протокола в популяцию CSR', true, NOW()),
    ('protocol', 'population', 'csr', 'disposition', 0.8, 
     'Популяция протокола также релевантна для disposition в CSR', true, NOW())
ON CONFLICT (from_doc_type, from_zone_key, to_doc_type, to_zone_key)
DO UPDATE SET 
    weight = EXCLUDED.weight,
    notes = EXCLUDED.notes,
    is_active = EXCLUDED.is_active;

