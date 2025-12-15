-- Примеры данных для ClinNexus MVP
-- Используйте эти примеры для тестирования и понимания структуры данных

-- ============================================================================
-- A) Multi-tenant / Auth
-- ============================================================================

-- Пример workspace
INSERT INTO workspaces (id, name, created_at) VALUES
('550e8400-e29b-41d4-a716-446655440000', 'Acme Pharma', NOW());

-- Пример пользователя
INSERT INTO users (id, email, name, is_active, created_at) VALUES
('660e8400-e29b-41d4-a716-446655440001', 'john.doe@acme.com', 'John Doe', true, NOW());

-- Пример membership
INSERT INTO memberships (id, workspace_id, user_id, role, created_at) VALUES
('770e8400-e29b-41d4-a716-446655440002', 
 '550e8400-e29b-41d4-a716-446655440000',
 '660e8400-e29b-41d4-a716-446655440001',
 'admin', NOW());

-- ============================================================================
-- B) Studies / Documents / Versions
-- ============================================================================

-- Пример study
INSERT INTO studies (id, workspace_id, study_code, title, status, created_at) VALUES
('880e8400-e29b-41d4-a716-446655440003',
 '550e8400-e29b-41d4-a716-446655440000',
 'STUDY-2024-001',
 'Phase III Trial for New Drug X',
 'active', NOW());

-- Пример document
INSERT INTO documents (id, workspace_id, study_id, doc_type, title, lifecycle_status, created_at) VALUES
('990e8400-e29b-41d4-a716-446655440004',
 '550e8400-e29b-41d4-a716-446655440000',
 '880e8400-e29b-41d4-a716-446655440003',
 'protocol',
 'Protocol v2.0',
 'approved', NOW());

-- Пример document_version
INSERT INTO document_versions (id, document_id, version_label, source_file_uri, source_sha256, 
                                effective_date, ingestion_status, ingestion_summary_json, 
                                created_by, created_at) VALUES
('aa0e8400-e29b-41d4-a716-446655440005',
 '990e8400-e29b-41d4-a716-446655440004',
 'v2.0',
 's3://bucket/protocol-v2.0.pdf',
 'a1b2c3d4e5f6...',
 '2024-01-15',
 'ready',
 '{"pages": 120, "sections": 45, "anchors": 1234, "chunks": 567}',
 '660e8400-e29b-41d4-a716-446655440001',
 NOW());

-- ============================================================================
-- E) Semantic section passports + mapping
-- ============================================================================

-- Пример section_contract для protocol.soa
INSERT INTO section_contracts (id, workspace_id, doc_type, section_key, title,
                                required_facts_json, allowed_sources_json, 
                                retrieval_recipe_json, qc_ruleset_json, 
                                citation_policy, version, is_active, created_at) VALUES
('bb0e8400-e29b-41d4-a716-446655440006',
 '550e8400-e29b-41d4-a716-446655440000',
 'protocol',
 'protocol.soa',
 'Schedule of Activities',
 '{
   "required": [
     {"fact_type": "visit_number", "description": "Номер визита"},
     {"fact_type": "visit_day", "description": "День визита"},
     {"fact_type": "procedures", "description": "Список процедур"}
   ]
 }',
 '{
   "allowed_doc_types": ["protocol"],
   "allowed_sections": ["protocol.soa", "protocol.methods"]
 }',
 '{
   "strategy": "structured_extraction",
   "prefer_anchors": true,
   "fallback_to_chunks": true
 }',
 '{
   "rules": [
     {"type": "completeness", "threshold": 0.95},
     {"type": "consistency", "check_cross_refs": true}
   ]
 }',
 'per_claim',
 1,
 true,
 NOW());

-- Пример section_map для конкретного doc_version_id
INSERT INTO section_maps (id, doc_version_id, section_key,
                          anchor_ids, chunk_ids,
                          confidence, status, mapped_by, notes, created_at) VALUES
('cc0e8400-e29b-41d4-a716-446655440007',
 'aa0e8400-e29b-41d4-a716-446655440005',
 'protocol.soa',
 ARRAY[
   'aa0e8400-e29b-41d4-a716-446655440005:3.2.1:p:1:hash123',
   'aa0e8400-e29b-41d4-a716-446655440005:3.2.1:tbl:1:hash456'
 ],
 ARRAY['dd0e8400-e29b-41d4-a716-446655440008'::uuid, 
       'ee0e8400-e29b-41d4-a716-446655440009'::uuid],
 0.92,
 'mapped',
 'system',
 'Автоматически сопоставлено на основе заголовков и структуры документа',
 NOW());

-- ============================================================================
-- Комментарии к ключевым концепциям
-- ============================================================================

-- section_path vs section_key:
-- 
-- section_path - путь по текущей структуре документа (например "3.2.1")
--   - Используется в anchors и chunks для навигации по документу
--   - Может меняться при обновлении структуры документа
--   - Пример: "3.2.1" означает "Раздел 3, подраздел 2, пункт 1"
--
-- section_key - семантический ключ секции (например "protocol.soa")
--   - Универсальный идентификатор, не зависящий от структуры документа
--   - Используется в section_contracts и section_maps
--   - Пример: "protocol.soa" означает "Schedule of Activities" в протоколе
--
-- section_contracts описывает требования к секции и НЕ хранит section_path
-- section_maps привязывает section_key к конкретным anchor_ids/chunk_ids
--   конкретной версии документа

