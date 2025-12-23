-- DROP SCHEMA public;

CREATE SCHEMA public AUTHORIZATION pg_database_owner;

COMMENT ON SCHEMA public IS 'standard public schema';

-- DROP TYPE public."anchor_content_type";

CREATE TYPE public."anchor_content_type" AS ENUM (
	'p',
	'cell',
	'fn',
	'hdr',
	'li',
	'tbl');

-- DROP TYPE public."citation_policy";

CREATE TYPE public."citation_policy" AS ENUM (
	'per_sentence',
	'per_claim',
	'none');

-- DROP TYPE public."conflict_severity";

CREATE TYPE public."conflict_severity" AS ENUM (
	'low',
	'medium',
	'high',
	'critical');

-- DROP TYPE public."conflict_status";

CREATE TYPE public."conflict_status" AS ENUM (
	'open',
	'investigating',
	'resolved',
	'accepted_risk',
	'suppressed');

-- DROP TYPE public."document_language";

CREATE TYPE public."document_language" AS ENUM (
	'ru',
	'en',
	'mixed',
	'unknown');

-- DROP TYPE public."document_lifecycle_status";

CREATE TYPE public."document_lifecycle_status" AS ENUM (
	'draft',
	'in_review',
	'approved',
	'superseded');

-- DROP TYPE public."document_type";

CREATE TYPE public."document_type" AS ENUM (
	'protocol',
	'sap',
	'tfl',
	'csr',
	'ib',
	'icf',
	'other');

-- DROP TYPE public."evidence_role";

CREATE TYPE public."evidence_role" AS ENUM (
	'primary',
	'supporting');

-- DROP TYPE public."fact_status";

CREATE TYPE public."fact_status" AS ENUM (
	'extracted',
	'validated',
	'conflicting',
	'tbd',
	'needs_review');

-- DROP TYPE public."generation_status";

CREATE TYPE public."generation_status" AS ENUM (
	'queued',
	'running',
	'blocked',
	'completed',
	'failed');

-- DROP TYPE public.halfvec;

CREATE TYPE public.halfvec (
	INPUT = halfvec_in,
	OUTPUT = halfvec_out,
	RECEIVE = halfvec_recv,
	SEND = halfvec_send,
	TYPMOD_IN = halfvec_typmod_in,
	ALIGNMENT = 4,
	STORAGE = secondary,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public."impact_status";

CREATE TYPE public."impact_status" AS ENUM (
	'pending',
	'applied',
	'rejected');

-- DROP TYPE public."ingestion_status";

CREATE TYPE public."ingestion_status" AS ENUM (
	'uploaded',
	'processing',
	'ready',
	'needs_review',
	'failed');

-- DROP TYPE public."qc_status";

CREATE TYPE public."qc_status" AS ENUM (
	'pass',
	'fail',
	'blocked');

-- DROP TYPE public."recommended_action";

CREATE TYPE public."recommended_action" AS ENUM (
	'auto_patch',
	'regenerate_draft',
	'manual_review');

-- DROP TYPE public."section_map_mapped_by";

CREATE TYPE public."section_map_mapped_by" AS ENUM (
	'system',
	'user');

-- DROP TYPE public."section_map_status";

CREATE TYPE public."section_map_status" AS ENUM (
	'mapped',
	'needs_review',
	'overridden');

-- DROP TYPE public."source_zone";

CREATE TYPE public."source_zone" AS ENUM (
	'overview',
	'design',
	'ip',
	'statistics',
	'safety',
	'endpoints',
	'population',
	'procedures',
	'data_management',
	'ethics',
	'admin',
	'appendix',
	'unknown');

-- DROP TYPE public.sparsevec;

CREATE TYPE public.sparsevec (
	INPUT = sparsevec_in,
	OUTPUT = sparsevec_out,
	RECEIVE = sparsevec_recv,
	SEND = sparsevec_send,
	TYPMOD_IN = sparsevec_typmod_in,
	ALIGNMENT = 4,
	STORAGE = secondary,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public."study_status";

CREATE TYPE public."study_status" AS ENUM (
	'active',
	'archived');

-- DROP TYPE public."task_status";

CREATE TYPE public."task_status" AS ENUM (
	'open',
	'in_progress',
	'done',
	'cancelled');

-- DROP TYPE public."task_type";

CREATE TYPE public."task_type" AS ENUM (
	'review_extraction',
	'resolve_conflict',
	'review_impact',
	'regenerate_section');

-- DROP TYPE public.vector;

CREATE TYPE public.vector (
	INPUT = vector_in,
	OUTPUT = vector_out,
	RECEIVE = vector_recv,
	SEND = vector_send,
	TYPMOD_IN = vector_typmod_in,
	ALIGNMENT = 4,
	STORAGE = secondary,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public."workspace_role";

CREATE TYPE public."workspace_role" AS ENUM (
	'admin',
	'writer',
	'clinops',
	'qa');
-- public.alembic_version определение

-- Drop table

-- DROP TABLE public.alembic_version;

CREATE TABLE public.alembic_version (
	version_num varchar(32) NOT NULL,
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);


-- public.model_configs определение

-- Drop table

-- DROP TABLE public.model_configs;

CREATE TABLE public.model_configs (
	id uuid NOT NULL,
	provider text NOT NULL,
	model_name text NOT NULL,
	prompt_version text NOT NULL,
	params_json jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_model_configs PRIMARY KEY (id)
);


-- public.section_taxonomy_aliases определение

-- Drop table

-- DROP TABLE public.section_taxonomy_aliases;

CREATE TABLE public.section_taxonomy_aliases (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_type varchar(50) NOT NULL,
	alias_key text NOT NULL,
	canonical_key text NOT NULL,
	reason text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_section_taxonomy_aliases PRIMARY KEY (id)
);
CREATE INDEX ix_section_taxonomy_aliases_canonical ON public.section_taxonomy_aliases USING btree (doc_type, canonical_key);
CREATE UNIQUE INDEX uq_section_taxonomy_aliases_doc_type_alias_key ON public.section_taxonomy_aliases USING btree (doc_type, alias_key);


-- public.section_taxonomy_nodes определение

-- Drop table

-- DROP TABLE public.section_taxonomy_nodes;

CREATE TABLE public.section_taxonomy_nodes (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_type varchar(50) NOT NULL,
	target_section text NOT NULL,
	title_ru text NOT NULL,
	parent_target_section text NULL,
	is_narrow bool DEFAULT false NOT NULL,
	expected_content jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_section_taxonomy_nodes PRIMARY KEY (id)
);
CREATE INDEX ix_section_taxonomy_nodes_parent ON public.section_taxonomy_nodes USING btree (doc_type, parent_target_section);
CREATE UNIQUE INDEX uq_section_taxonomy_nodes_doc_type_section_key ON public.section_taxonomy_nodes USING btree (doc_type, target_section);


-- public.section_taxonomy_related определение

-- Drop table

-- DROP TABLE public.section_taxonomy_related;

CREATE TABLE public.section_taxonomy_related (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_type varchar(50) NOT NULL,
	a_target_section text NOT NULL,
	b_target_section text NOT NULL,
	reason text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_section_taxonomy_related PRIMARY KEY (id)
);
CREATE INDEX ix_section_taxonomy_related_a ON public.section_taxonomy_related USING btree (doc_type, a_target_section);
CREATE INDEX ix_section_taxonomy_related_b ON public.section_taxonomy_related USING btree (doc_type, b_target_section);
CREATE UNIQUE INDEX uq_section_taxonomy_related_doc_type_ab ON public.section_taxonomy_related USING btree (doc_type, a_target_section, b_target_section);


-- public.users определение

-- Drop table

-- DROP TABLE public.users;

CREATE TABLE public.users (
	id uuid NOT NULL,
	email varchar(320) NOT NULL,
	"name" text NULL,
	is_active bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_users PRIMARY KEY (id),
	CONSTRAINT uq_users_email UNIQUE (email)
);


-- public.workspaces определение

-- Drop table

-- DROP TABLE public.workspaces;

CREATE TABLE public.workspaces (
	id uuid NOT NULL,
	"name" text NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_workspaces PRIMARY KEY (id)
);


-- public.audit_log определение

-- Drop table

-- DROP TABLE public.audit_log;

CREATE TABLE public.audit_log (
	id uuid NOT NULL,
	workspace_id uuid NOT NULL,
	actor_user_id uuid NULL,
	"action" text NOT NULL,
	entity_type text NOT NULL,
	entity_id varchar(128) NOT NULL,
	before_json jsonb NULL,
	after_json jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_audit_log PRIMARY KEY (id),
	CONSTRAINT fk_audit_log_actor_user_id_users FOREIGN KEY (actor_user_id) REFERENCES public.users(id) ON DELETE SET NULL,
	CONSTRAINT fk_audit_log_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE
);
CREATE INDEX ix_audit_log_entity ON public.audit_log USING btree (entity_type, entity_id);
CREATE INDEX ix_audit_log_workspace_created_at ON public.audit_log USING btree (workspace_id, created_at DESC);


-- public.memberships определение

-- Drop table

-- DROP TABLE public.memberships;

CREATE TABLE public.memberships (
	id uuid NOT NULL,
	workspace_id uuid NOT NULL,
	user_id uuid NOT NULL,
	"role" public."workspace_role" NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_memberships PRIMARY KEY (id),
	CONSTRAINT uq_memberships_workspace_user UNIQUE (workspace_id, user_id),
	CONSTRAINT fk_memberships_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
	CONSTRAINT fk_memberships_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE
);


-- public.section_contracts определение

-- Drop table

-- DROP TABLE public.section_contracts;

CREATE TABLE public.section_contracts (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	workspace_id uuid NOT NULL,
	doc_type public."document_type" NOT NULL,
	target_section text NOT NULL,
	title text NOT NULL,
	required_facts_json jsonb NOT NULL,
	allowed_sources_json jsonb NOT NULL,
	retrieval_recipe_json jsonb NOT NULL,
	qc_ruleset_json jsonb NOT NULL,
	"citation_policy" public."citation_policy" NOT NULL,
	"version" int4 NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	view_key text NULL,
	CONSTRAINT pk_section_contracts PRIMARY KEY (id),
	CONSTRAINT uq_section_contracts_ws_doc_type_key_version UNIQUE (workspace_id, doc_type, target_section, version),
	CONSTRAINT fk_section_contracts_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE
);


-- public.studies определение

-- Drop table

-- DROP TABLE public.studies;

CREATE TABLE public.studies (
	id uuid NOT NULL,
	workspace_id uuid NOT NULL,
	study_code text NOT NULL,
	title text NOT NULL,
	status public."study_status" NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_studies PRIMARY KEY (id),
	CONSTRAINT uq_studies_workspace_code UNIQUE (workspace_id, study_code),
	CONSTRAINT fk_studies_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE
);


-- public.tasks определение

-- Drop table

-- DROP TABLE public.tasks;

CREATE TABLE public.tasks (
	id uuid NOT NULL,
	study_id uuid NOT NULL,
	"type" public."task_type" NOT NULL,
	status public."task_status" NOT NULL,
	assigned_to uuid NULL,
	payload_json jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_tasks PRIMARY KEY (id),
	CONSTRAINT fk_tasks_assigned_to_users FOREIGN KEY (assigned_to) REFERENCES public.users(id) ON DELETE SET NULL,
	CONSTRAINT fk_tasks_study_id_studies FOREIGN KEY (study_id) REFERENCES public.studies(id) ON DELETE CASCADE
);
CREATE INDEX ix_tasks_status ON public.tasks USING btree (status);
CREATE INDEX ix_tasks_type ON public.tasks USING btree (type);


-- public.templates определение

-- Drop table

-- DROP TABLE public.templates;

CREATE TABLE public.templates (
	id uuid NOT NULL,
	workspace_id uuid NOT NULL,
	doc_type public."document_type" NOT NULL,
	"name" text NOT NULL,
	template_body text NOT NULL,
	"version" int4 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_templates PRIMARY KEY (id),
	CONSTRAINT uq_templates_ws_doc_type_name_version UNIQUE (workspace_id, doc_type, name, version),
	CONSTRAINT fk_templates_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE
);


-- public.topics определение

-- Drop table

-- DROP TABLE public.topics;

CREATE TABLE public.topics (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	workspace_id uuid NOT NULL,
	topic_key text NOT NULL,
	title_ru text NULL,
	title_en text NULL,
	description text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	topic_profile_json jsonb DEFAULT '{}'::jsonb NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	topic_embedding public.vector NULL,
	CONSTRAINT pk_topics PRIMARY KEY (id),
	CONSTRAINT uq_topics_workspace_topic_key UNIQUE (workspace_id, topic_key),
	CONSTRAINT fk_topics_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE
);
CREATE INDEX ix_topics_is_active ON public.topics USING btree (is_active);
CREATE INDEX ix_topics_topic_profile_json ON public.topics USING gin (topic_profile_json);
CREATE INDEX ix_topics_workspace_is_active ON public.topics USING btree (workspace_id, is_active);


-- public.conflicts определение

-- Drop table

-- DROP TABLE public.conflicts;

CREATE TABLE public.conflicts (
	id uuid NOT NULL,
	study_id uuid NOT NULL,
	conflict_type text NOT NULL,
	severity public."conflict_severity" NOT NULL,
	status public."conflict_status" NOT NULL,
	title text NOT NULL,
	description text NOT NULL,
	owner_user_id uuid NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_conflicts PRIMARY KEY (id),
	CONSTRAINT fk_conflicts_owner_user_id_users FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE SET NULL,
	CONSTRAINT fk_conflicts_study_id_studies FOREIGN KEY (study_id) REFERENCES public.studies(id) ON DELETE CASCADE
);
CREATE INDEX ix_conflicts_severity ON public.conflicts USING btree (severity);
CREATE INDEX ix_conflicts_status ON public.conflicts USING btree (status);
CREATE INDEX ix_conflicts_study_status ON public.conflicts USING btree (study_id, status);


-- public.documents определение

-- Drop table

-- DROP TABLE public.documents;

CREATE TABLE public.documents (
	id uuid NOT NULL,
	workspace_id uuid NOT NULL,
	study_id uuid NOT NULL,
	doc_type public."document_type" NOT NULL,
	title text NOT NULL,
	lifecycle_status public."document_lifecycle_status" NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_documents PRIMARY KEY (id),
	CONSTRAINT fk_documents_study_id_studies FOREIGN KEY (study_id) REFERENCES public.studies(id) ON DELETE CASCADE,
	CONSTRAINT fk_documents_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE
);


-- public.generation_runs определение

-- Drop table

-- DROP TABLE public.generation_runs;

CREATE TABLE public.generation_runs (
	id uuid NOT NULL,
	study_id uuid NOT NULL,
	target_doc_type varchar NOT NULL,
	target_section text NOT NULL,
	template_id uuid NOT NULL,
	contract_id uuid NOT NULL,
	input_snapshot_json jsonb NOT NULL,
	model_config_id uuid NULL,
	status public."generation_status" NOT NULL,
	created_by uuid NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	view_key text NULL,
	CONSTRAINT pk_generation_runs PRIMARY KEY (id),
	CONSTRAINT fk_generation_runs_contract_id_section_contracts FOREIGN KEY (contract_id) REFERENCES public.section_contracts(id) ON DELETE RESTRICT,
	CONSTRAINT fk_generation_runs_created_by_users FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL,
	CONSTRAINT fk_generation_runs_model_config_id_model_configs FOREIGN KEY (model_config_id) REFERENCES public.model_configs(id) ON DELETE SET NULL,
	CONSTRAINT fk_generation_runs_study_id_studies FOREIGN KEY (study_id) REFERENCES public.studies(id) ON DELETE CASCADE,
	CONSTRAINT fk_generation_runs_template_id_templates FOREIGN KEY (template_id) REFERENCES public.templates(id) ON DELETE RESTRICT
);
CREATE INDEX ix_generation_runs_status ON public.generation_runs USING btree (status);


-- public.anchor_matches определение

-- Drop table

-- DROP TABLE public.anchor_matches;

CREATE TABLE public.anchor_matches (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	document_id uuid NOT NULL,
	from_doc_version_id uuid NOT NULL,
	to_doc_version_id uuid NOT NULL,
	from_anchor_id text NOT NULL,
	to_anchor_id text NOT NULL,
	score float8 NOT NULL,
	"method" text NOT NULL,
	meta_json jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_anchor_matches PRIMARY KEY (id),
	CONSTRAINT uq_anchor_matches_version_from_anchor UNIQUE (from_doc_version_id, to_doc_version_id, from_anchor_id)
);
CREATE INDEX ix_anchor_matches_document_id ON public.anchor_matches USING btree (document_id);
CREATE INDEX ix_anchor_matches_to_anchor ON public.anchor_matches USING btree (to_doc_version_id, to_anchor_id);
CREATE INDEX ix_anchor_matches_versions ON public.anchor_matches USING btree (from_doc_version_id, to_doc_version_id);


-- public.anchors определение

-- Drop table

-- DROP TABLE public.anchors;

CREATE TABLE public.anchors (
	id uuid NOT NULL,
	doc_version_id uuid NOT NULL,
	anchor_id varchar(512) NOT NULL,
	section_path text NOT NULL,
	content_type public."anchor_content_type" NOT NULL,
	ordinal int4 NOT NULL,
	text_raw text NOT NULL,
	text_norm text NOT NULL,
	text_hash varchar(64) NOT NULL,
	location_json jsonb NOT NULL,
	confidence float8 NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	"source_zone" public."source_zone" DEFAULT 'unknown'::source_zone NOT NULL,
	"language" public."document_language" DEFAULT 'unknown'::document_language NOT NULL,
	CONSTRAINT pk_anchors PRIMARY KEY (id),
	CONSTRAINT uq_anchors_anchor_id UNIQUE (anchor_id)
);
CREATE INDEX ix_anchors_doc_version_content_type ON public.anchors USING btree (doc_version_id, content_type);
CREATE INDEX ix_anchors_doc_version_language ON public.anchors USING btree (doc_version_id, language);
CREATE INDEX ix_anchors_doc_version_section_path ON public.anchors USING btree (doc_version_id, section_path);
CREATE INDEX ix_anchors_doc_version_source_zone ON public.anchors USING btree (doc_version_id, source_zone);


-- public.change_events определение

-- Drop table

-- DROP TABLE public.change_events;

CREATE TABLE public.change_events (
	id uuid NOT NULL,
	study_id uuid NOT NULL,
	source_document_id uuid NOT NULL,
	from_version_id uuid NOT NULL,
	to_version_id uuid NOT NULL,
	diff_summary_json jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_change_events PRIMARY KEY (id)
);


-- public.chunks определение

-- Drop table

-- DROP TABLE public.chunks;

CREATE TABLE public.chunks (
	id uuid NOT NULL,
	doc_version_id uuid NOT NULL,
	chunk_id varchar(512) NOT NULL,
	section_path text NOT NULL,
	"text" text NOT NULL,
	anchor_ids _text NOT NULL,
	embedding public.vector NOT NULL,
	metadata_json jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	"source_zone" public."source_zone" DEFAULT 'unknown'::source_zone NOT NULL,
	"language" public."document_language" DEFAULT 'unknown'::document_language NOT NULL,
	CONSTRAINT pk_chunks PRIMARY KEY (id),
	CONSTRAINT uq_chunks_chunk_id UNIQUE (chunk_id)
);
CREATE INDEX idx_chunks_embedding_hnsw ON public.chunks USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');
CREATE INDEX ix_chunks_doc_version_language ON public.chunks USING btree (doc_version_id, language);
CREATE INDEX ix_chunks_doc_version_section_path ON public.chunks USING btree (doc_version_id, section_path);
CREATE INDEX ix_chunks_doc_version_source_zone ON public.chunks USING btree (doc_version_id, source_zone);


-- public.cluster_assignments определение

-- Drop table

-- DROP TABLE public.cluster_assignments;

CREATE TABLE public.cluster_assignments (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_version_id uuid NOT NULL,
	cluster_id int4 NOT NULL,
	topic_key text NOT NULL,
	mapped_by varchar(50) NOT NULL,
	confidence float8 NULL,
	notes text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	mapping_debug_json jsonb NULL,
	CONSTRAINT ck_cluster_assignments_ck_cluster_assignments_mapped_by CHECK (((mapped_by)::text = ANY ((ARRAY['auto'::character varying, 'assist'::character varying, 'manual'::character varying, 'seed'::character varying, 'import'::character varying])::text[]))),
	CONSTRAINT pk_cluster_assignments PRIMARY KEY (id),
	CONSTRAINT uq_cluster_assignments_doc_version_cluster UNIQUE (doc_version_id, cluster_id)
);
CREATE INDEX ix_cluster_assignments_doc_version_id ON public.cluster_assignments USING btree (doc_version_id);
CREATE INDEX ix_cluster_assignments_topic_key ON public.cluster_assignments USING btree (topic_key);


-- public.conflict_items определение

-- Drop table

-- DROP TABLE public.conflict_items;

CREATE TABLE public.conflict_items (
	id uuid NOT NULL,
	conflict_id uuid NOT NULL,
	left_anchor_id text NULL,
	right_anchor_id text NULL,
	left_fact_id uuid NULL,
	right_fact_id uuid NULL,
	evidence_json jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_conflict_items PRIMARY KEY (id)
);


-- public.document_versions определение

-- Drop table

-- DROP TABLE public.document_versions;

CREATE TABLE public.document_versions (
	id uuid NOT NULL,
	document_id uuid NOT NULL,
	version_label varchar(64) NOT NULL,
	source_file_uri text NULL,
	source_sha256 varchar(64) NULL,
	effective_date date NULL,
	"ingestion_status" public."ingestion_status" NOT NULL,
	ingestion_summary_json jsonb NULL,
	created_by uuid NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	"document_language" public."document_language" DEFAULT 'unknown'::document_language NOT NULL,
	last_ingestion_run_id uuid NULL,
	CONSTRAINT pk_document_versions PRIMARY KEY (id)
);
CREATE INDEX ix_document_versions_document_created_at ON public.document_versions USING btree (document_id, created_at DESC);
CREATE INDEX ix_document_versions_ingestion_status ON public.document_versions USING btree (ingestion_status);


-- public.fact_evidence определение

-- Drop table

-- DROP TABLE public.fact_evidence;

CREATE TABLE public.fact_evidence (
	id uuid NOT NULL,
	fact_id uuid NOT NULL,
	anchor_id text NOT NULL,
	"evidence_role" public."evidence_role" NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_fact_evidence PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_fact_evidence_fact_anchor_role ON public.fact_evidence USING btree (fact_id, anchor_id, evidence_role);


-- public.facts определение

-- Drop table

-- DROP TABLE public.facts;

CREATE TABLE public.facts (
	id uuid NOT NULL,
	study_id uuid NOT NULL,
	fact_type text NOT NULL,
	fact_key text NOT NULL,
	value_json jsonb NOT NULL,
	unit text NULL,
	status public."fact_status" NOT NULL,
	created_from_doc_version_id uuid NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	confidence float8 NULL,
	extractor_version int4 NULL,
	meta_json jsonb NULL,
	CONSTRAINT pk_facts PRIMARY KEY (id),
	CONSTRAINT uq_facts_study_type_key UNIQUE (study_id, fact_type, fact_key)
);
CREATE INDEX ix_facts_confidence ON public.facts USING btree (confidence);
CREATE INDEX ix_facts_status ON public.facts USING btree (status);
CREATE INDEX ix_facts_study_fact_type ON public.facts USING btree (study_id, fact_type);


-- public.generated_sections определение

-- Drop table

-- DROP TABLE public.generated_sections;

CREATE TABLE public.generated_sections (
	id uuid NOT NULL,
	generation_run_id uuid NOT NULL,
	content_text text NOT NULL,
	artifacts_json jsonb NOT NULL,
	"qc_status" public."qc_status" NOT NULL,
	qc_report_json jsonb NOT NULL,
	published_to_document_version_id uuid NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_generated_sections PRIMARY KEY (id)
);
CREATE INDEX ix_generated_sections_qc_status ON public.generated_sections USING btree (qc_status);


-- public.heading_clusters определение

-- Drop table

-- DROP TABLE public.heading_clusters;

CREATE TABLE public.heading_clusters (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_version_id uuid NOT NULL,
	cluster_id int4 NOT NULL,
	"language" public."document_language" NOT NULL,
	top_titles_json jsonb DEFAULT '[]'::jsonb NOT NULL,
	examples_json jsonb DEFAULT '[]'::jsonb NOT NULL,
	stats_json jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	cluster_embedding public.vector NULL,
	CONSTRAINT pk_heading_clusters PRIMARY KEY (id),
	CONSTRAINT uq_heading_clusters_doc_version_cluster_language UNIQUE (doc_version_id, cluster_id, language)
);
CREATE INDEX ix_heading_clusters_cluster_id ON public.heading_clusters USING btree (cluster_id);
CREATE INDEX ix_heading_clusters_doc_version_cluster ON public.heading_clusters USING btree (doc_version_id, cluster_id);
CREATE INDEX ix_heading_clusters_doc_version_id ON public.heading_clusters USING btree (doc_version_id);


-- public.impact_items определение

-- Drop table

-- DROP TABLE public.impact_items;

CREATE TABLE public.impact_items (
	id uuid NOT NULL,
	change_event_id uuid NOT NULL,
	affected_doc_type varchar NOT NULL,
	affected_target_section text NOT NULL,
	reason_json jsonb NOT NULL,
	"recommended_action" public."recommended_action" NOT NULL,
	status public."impact_status" NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_impact_items PRIMARY KEY (id)
);


-- public.ingestion_runs определение

-- Drop table

-- DROP TABLE public.ingestion_runs;

CREATE TABLE public.ingestion_runs (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_version_id uuid NOT NULL,
	status text NOT NULL,
	started_at timestamptz DEFAULT now() NOT NULL,
	finished_at timestamptz NULL,
	duration_ms int4 NULL,
	pipeline_version text NOT NULL,
	pipeline_config_hash text NOT NULL,
	summary_json jsonb DEFAULT '{}'::jsonb NOT NULL,
	quality_json jsonb DEFAULT '{}'::jsonb NOT NULL,
	warnings_json jsonb DEFAULT '[]'::jsonb NOT NULL,
	errors_json jsonb DEFAULT '[]'::jsonb NOT NULL,
	CONSTRAINT chk_ingestion_runs_status CHECK ((status = ANY (ARRAY['ok'::text, 'failed'::text, 'partial'::text]))),
	CONSTRAINT pk_ingestion_runs PRIMARY KEY (id)
);
CREATE INDEX idx_ingestion_runs_doc_version_id ON public.ingestion_runs USING btree (doc_version_id);
CREATE INDEX idx_ingestion_runs_started_at ON public.ingestion_runs USING btree (started_at);


-- public.section_maps определение

-- Drop table

-- DROP TABLE public.section_maps;

CREATE TABLE public.section_maps (
	id uuid NOT NULL,
	doc_version_id uuid NOT NULL,
	target_section text NOT NULL,
	anchor_ids _text NULL,
	chunk_ids _uuid NULL,
	confidence float8 NOT NULL,
	status public."section_map_status" NOT NULL,
	mapped_by public."section_map_mapped_by" NOT NULL,
	notes text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_section_maps PRIMARY KEY (id),
	CONSTRAINT uq_section_maps_doc_version_section_key UNIQUE (doc_version_id, target_section)
);
CREATE INDEX ix_section_maps_status ON public.section_maps USING btree (status);


-- public.study_core_facts определение

-- Drop table

-- DROP TABLE public.study_core_facts;

CREATE TABLE public.study_core_facts (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	study_id uuid NOT NULL,
	doc_version_id uuid NULL,
	facts_json jsonb NOT NULL,
	facts_version int4 DEFAULT 1 NOT NULL,
	derived_from_doc_version_id uuid NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_study_core_facts PRIMARY KEY (id)
);
CREATE INDEX ix_study_core_facts_doc_version ON public.study_core_facts USING btree (doc_version_id);
CREATE INDEX ix_study_core_facts_study_version ON public.study_core_facts USING btree (study_id, facts_version);


-- public.topic_evidence определение

-- Drop table

-- DROP TABLE public.topic_evidence;

CREATE TABLE public.topic_evidence (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_version_id uuid NOT NULL,
	topic_key text NOT NULL,
	"source_zone" text NOT NULL,
	"language" public."document_language" NOT NULL,
	anchor_ids _text NOT NULL,
	chunk_ids _uuid NOT NULL,
	score float8 NULL,
	evidence_json jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT pk_topic_evidence PRIMARY KEY (id),
	CONSTRAINT uq_topic_evidence_doc_version_topic_source_lang UNIQUE (doc_version_id, topic_key, source_zone, language)
);
CREATE INDEX ix_topic_evidence_topic_key ON public.topic_evidence USING btree (topic_key);


-- public.topic_mapping_runs определение

-- Drop table

-- DROP TABLE public.topic_mapping_runs;

CREATE TABLE public.topic_mapping_runs (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	doc_version_id uuid NOT NULL,
	"mode" text NOT NULL,
	params_json jsonb DEFAULT '{}'::jsonb NOT NULL,
	metrics_json jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	pipeline_version text NOT NULL,
	pipeline_config_hash text NOT NULL,
	CONSTRAINT pk_topic_mapping_runs PRIMARY KEY (id)
);
CREATE INDEX ix_topic_mapping_runs_created_at ON public.topic_mapping_runs USING btree (created_at);
CREATE INDEX ix_topic_mapping_runs_doc_version_id ON public.topic_mapping_runs USING btree (doc_version_id);


-- public.anchor_matches внешние включи

ALTER TABLE public.anchor_matches ADD CONSTRAINT fk_anchor_matches_document_id_documents FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;
ALTER TABLE public.anchor_matches ADD CONSTRAINT fk_anchor_matches_from_doc_version_id_document_versions FOREIGN KEY (from_doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;
ALTER TABLE public.anchor_matches ADD CONSTRAINT fk_anchor_matches_to_doc_version_id_document_versions FOREIGN KEY (to_doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.anchors внешние включи

ALTER TABLE public.anchors ADD CONSTRAINT fk_anchors_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.change_events внешние включи

ALTER TABLE public.change_events ADD CONSTRAINT fk_change_events_from_version_id_document_versions FOREIGN KEY (from_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;
ALTER TABLE public.change_events ADD CONSTRAINT fk_change_events_source_document_id_documents FOREIGN KEY (source_document_id) REFERENCES public.documents(id) ON DELETE CASCADE;
ALTER TABLE public.change_events ADD CONSTRAINT fk_change_events_study_id_studies FOREIGN KEY (study_id) REFERENCES public.studies(id) ON DELETE CASCADE;
ALTER TABLE public.change_events ADD CONSTRAINT fk_change_events_to_version_id_document_versions FOREIGN KEY (to_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.chunks внешние включи

ALTER TABLE public.chunks ADD CONSTRAINT fk_chunks_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.cluster_assignments внешние включи

ALTER TABLE public.cluster_assignments ADD CONSTRAINT fk_cluster_assignments_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.conflict_items внешние включи

ALTER TABLE public.conflict_items ADD CONSTRAINT fk_conflict_items_conflict_id_conflicts FOREIGN KEY (conflict_id) REFERENCES public.conflicts(id) ON DELETE CASCADE;
ALTER TABLE public.conflict_items ADD CONSTRAINT fk_conflict_items_left_fact_id_facts FOREIGN KEY (left_fact_id) REFERENCES public.facts(id) ON DELETE SET NULL;
ALTER TABLE public.conflict_items ADD CONSTRAINT fk_conflict_items_right_fact_id_facts FOREIGN KEY (right_fact_id) REFERENCES public.facts(id) ON DELETE SET NULL;


-- public.document_versions внешние включи

ALTER TABLE public.document_versions ADD CONSTRAINT fk_document_versions_created_by_users FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;
ALTER TABLE public.document_versions ADD CONSTRAINT fk_document_versions_document_id_documents FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;
ALTER TABLE public.document_versions ADD CONSTRAINT fk_document_versions_last_ingestion_run_id_ingestion_runs FOREIGN KEY (last_ingestion_run_id) REFERENCES public.ingestion_runs(id) ON DELETE SET NULL;


-- public.fact_evidence внешние включи

ALTER TABLE public.fact_evidence ADD CONSTRAINT fk_fact_evidence_fact_id_facts FOREIGN KEY (fact_id) REFERENCES public.facts(id) ON DELETE CASCADE;


-- public.facts внешние включи

ALTER TABLE public.facts ADD CONSTRAINT fk_facts_created_from_doc_version_id_document_versions FOREIGN KEY (created_from_doc_version_id) REFERENCES public.document_versions(id) ON DELETE SET NULL;
ALTER TABLE public.facts ADD CONSTRAINT fk_facts_study_id_studies FOREIGN KEY (study_id) REFERENCES public.studies(id) ON DELETE CASCADE;


-- public.generated_sections внешние включи

ALTER TABLE public.generated_sections ADD CONSTRAINT fk_generated_sections_generation_run_id_generation_runs FOREIGN KEY (generation_run_id) REFERENCES public.generation_runs(id) ON DELETE CASCADE;
ALTER TABLE public.generated_sections ADD CONSTRAINT fk_generated_sections_published_to_document_version_id__ea45 FOREIGN KEY (published_to_document_version_id) REFERENCES public.document_versions(id) ON DELETE SET NULL;


-- public.heading_clusters внешние включи

ALTER TABLE public.heading_clusters ADD CONSTRAINT fk_heading_clusters_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.impact_items внешние включи

ALTER TABLE public.impact_items ADD CONSTRAINT fk_impact_items_change_event_id_change_events FOREIGN KEY (change_event_id) REFERENCES public.change_events(id) ON DELETE CASCADE;


-- public.ingestion_runs внешние включи

ALTER TABLE public.ingestion_runs ADD CONSTRAINT fk_ingestion_runs_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.section_maps внешние включи

ALTER TABLE public.section_maps ADD CONSTRAINT fk_section_maps_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.study_core_facts внешние включи

ALTER TABLE public.study_core_facts ADD CONSTRAINT fk_study_core_facts_derived_from_doc_version_id_documen_e528 FOREIGN KEY (derived_from_doc_version_id) REFERENCES public.document_versions(id) ON DELETE SET NULL;
ALTER TABLE public.study_core_facts ADD CONSTRAINT fk_study_core_facts_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE SET NULL;
ALTER TABLE public.study_core_facts ADD CONSTRAINT fk_study_core_facts_study_id_studies FOREIGN KEY (study_id) REFERENCES public.studies(id) ON DELETE CASCADE;


-- public.topic_evidence внешние включи

ALTER TABLE public.topic_evidence ADD CONSTRAINT fk_topic_evidence_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;


-- public.topic_mapping_runs внешние включи

ALTER TABLE public.topic_mapping_runs ADD CONSTRAINT fk_topic_mapping_runs_doc_version_id_document_versions FOREIGN KEY (doc_version_id) REFERENCES public.document_versions(id) ON DELETE CASCADE;



-- DROP FUNCTION public.array_to_halfvec(_numeric, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_halfvec(numeric[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;

-- DROP FUNCTION public.array_to_halfvec(_int4, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_halfvec(integer[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;

-- DROP FUNCTION public.array_to_halfvec(_float4, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_halfvec(real[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;

-- DROP FUNCTION public.array_to_halfvec(_float8, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_halfvec(double precision[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;

-- DROP FUNCTION public.array_to_sparsevec(_int4, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_sparsevec(integer[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;

-- DROP FUNCTION public.array_to_sparsevec(_numeric, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_sparsevec(numeric[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;

-- DROP FUNCTION public.array_to_sparsevec(_float8, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_sparsevec(double precision[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;

-- DROP FUNCTION public.array_to_sparsevec(_float4, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_sparsevec(real[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;

-- DROP FUNCTION public.array_to_vector(_float4, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_vector(real[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;

-- DROP FUNCTION public.array_to_vector(_numeric, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_vector(numeric[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;

-- DROP FUNCTION public.array_to_vector(_float8, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_vector(double precision[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;

-- DROP FUNCTION public.array_to_vector(_int4, int4, bool);

CREATE OR REPLACE FUNCTION public.array_to_vector(integer[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;

-- DROP AGGREGATE public.avg(halfvec);

-- Aggregate function public.avg(halfvec)
-- ERROR: more than one function named "public.avg";

-- DROP AGGREGATE public.avg(vector);

-- Aggregate function public.avg(vector)
-- ERROR: more than one function named "public.avg";

-- DROP FUNCTION public.binary_quantize(vector);

CREATE OR REPLACE FUNCTION public.binary_quantize(vector)
 RETURNS bit
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$binary_quantize$function$
;

-- DROP FUNCTION public.binary_quantize(halfvec);

CREATE OR REPLACE FUNCTION public.binary_quantize(halfvec)
 RETURNS bit
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_binary_quantize$function$
;

-- DROP FUNCTION public.cosine_distance(vector, vector);

CREATE OR REPLACE FUNCTION public.cosine_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$cosine_distance$function$
;

-- DROP FUNCTION public.cosine_distance(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.cosine_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_cosine_distance$function$
;

-- DROP FUNCTION public.cosine_distance(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.cosine_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_cosine_distance$function$
;

-- DROP FUNCTION public.halfvec(halfvec, int4, bool);

CREATE OR REPLACE FUNCTION public.halfvec(halfvec, integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec$function$
;

-- DROP FUNCTION public.halfvec_accum(_float8, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_accum(double precision[], halfvec)
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_accum$function$
;

-- DROP FUNCTION public.halfvec_add(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_add(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_add$function$
;

-- DROP FUNCTION public.halfvec_avg(_float8);

CREATE OR REPLACE FUNCTION public.halfvec_avg(double precision[])
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_avg$function$
;

-- DROP FUNCTION public.halfvec_cmp(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_cmp(halfvec, halfvec)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_cmp$function$
;

-- DROP FUNCTION public.halfvec_combine(_float8, _float8);

CREATE OR REPLACE FUNCTION public.halfvec_combine(double precision[], double precision[])
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_combine$function$
;

-- DROP FUNCTION public.halfvec_concat(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_concat(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_concat$function$
;

-- DROP FUNCTION public.halfvec_eq(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_eq(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_eq$function$
;

-- DROP FUNCTION public.halfvec_ge(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_ge(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_ge$function$
;

-- DROP FUNCTION public.halfvec_gt(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_gt(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_gt$function$
;

-- DROP FUNCTION public.halfvec_in(cstring, oid, int4);

CREATE OR REPLACE FUNCTION public.halfvec_in(cstring, oid, integer)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_in$function$
;

-- DROP FUNCTION public.halfvec_l2_squared_distance(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_l2_squared_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_squared_distance$function$
;

-- DROP FUNCTION public.halfvec_le(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_le(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_le$function$
;

-- DROP FUNCTION public.halfvec_lt(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_lt(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_lt$function$
;

-- DROP FUNCTION public.halfvec_mul(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_mul(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_mul$function$
;

-- DROP FUNCTION public.halfvec_ne(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_ne(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_ne$function$
;

-- DROP FUNCTION public.halfvec_negative_inner_product(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_negative_inner_product(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_negative_inner_product$function$
;

-- DROP FUNCTION public.halfvec_out(halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_out(halfvec)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_out$function$
;

-- DROP FUNCTION public.halfvec_recv(internal, oid, int4);

CREATE OR REPLACE FUNCTION public.halfvec_recv(internal, oid, integer)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_recv$function$
;

-- DROP FUNCTION public.halfvec_send(halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_send(halfvec)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_send$function$
;

-- DROP FUNCTION public.halfvec_spherical_distance(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_spherical_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_spherical_distance$function$
;

-- DROP FUNCTION public.halfvec_sub(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.halfvec_sub(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_sub$function$
;

-- DROP FUNCTION public.halfvec_to_float4(halfvec, int4, bool);

CREATE OR REPLACE FUNCTION public.halfvec_to_float4(halfvec, integer, boolean)
 RETURNS real[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_to_float4$function$
;

-- DROP FUNCTION public.halfvec_to_sparsevec(halfvec, int4, bool);

CREATE OR REPLACE FUNCTION public.halfvec_to_sparsevec(halfvec, integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_to_sparsevec$function$
;

-- DROP FUNCTION public.halfvec_to_vector(halfvec, int4, bool);

CREATE OR REPLACE FUNCTION public.halfvec_to_vector(halfvec, integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_to_vector$function$
;

-- DROP FUNCTION public.halfvec_typmod_in(_cstring);

CREATE OR REPLACE FUNCTION public.halfvec_typmod_in(cstring[])
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_typmod_in$function$
;

-- DROP FUNCTION public.hamming_distance(bit, bit);

CREATE OR REPLACE FUNCTION public.hamming_distance(bit, bit)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$hamming_distance$function$
;

-- DROP FUNCTION public.hnsw_bit_support(internal);

CREATE OR REPLACE FUNCTION public.hnsw_bit_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$hnsw_bit_support$function$
;

-- DROP FUNCTION public.hnsw_halfvec_support(internal);

CREATE OR REPLACE FUNCTION public.hnsw_halfvec_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$hnsw_halfvec_support$function$
;

-- DROP FUNCTION public.hnsw_sparsevec_support(internal);

CREATE OR REPLACE FUNCTION public.hnsw_sparsevec_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$hnsw_sparsevec_support$function$
;

-- DROP FUNCTION public.hnswhandler(internal);

CREATE OR REPLACE FUNCTION public.hnswhandler(internal)
 RETURNS index_am_handler
 LANGUAGE c
AS '$libdir/vector', $function$hnswhandler$function$
;

-- DROP FUNCTION public.inner_product(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.inner_product(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_inner_product$function$
;

-- DROP FUNCTION public.inner_product(vector, vector);

CREATE OR REPLACE FUNCTION public.inner_product(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$inner_product$function$
;

-- DROP FUNCTION public.inner_product(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.inner_product(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_inner_product$function$
;

-- DROP FUNCTION public.ivfflat_bit_support(internal);

CREATE OR REPLACE FUNCTION public.ivfflat_bit_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$ivfflat_bit_support$function$
;

-- DROP FUNCTION public.ivfflat_halfvec_support(internal);

CREATE OR REPLACE FUNCTION public.ivfflat_halfvec_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$ivfflat_halfvec_support$function$
;

-- DROP FUNCTION public.ivfflathandler(internal);

CREATE OR REPLACE FUNCTION public.ivfflathandler(internal)
 RETURNS index_am_handler
 LANGUAGE c
AS '$libdir/vector', $function$ivfflathandler$function$
;

-- DROP FUNCTION public.jaccard_distance(bit, bit);

CREATE OR REPLACE FUNCTION public.jaccard_distance(bit, bit)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$jaccard_distance$function$
;

-- DROP FUNCTION public.l1_distance(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.l1_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l1_distance$function$
;

-- DROP FUNCTION public.l1_distance(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.l1_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l1_distance$function$
;

-- DROP FUNCTION public.l1_distance(vector, vector);

CREATE OR REPLACE FUNCTION public.l1_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$l1_distance$function$
;

-- DROP FUNCTION public.l2_distance(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.l2_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_distance$function$
;

-- DROP FUNCTION public.l2_distance(halfvec, halfvec);

CREATE OR REPLACE FUNCTION public.l2_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_distance$function$
;

-- DROP FUNCTION public.l2_distance(vector, vector);

CREATE OR REPLACE FUNCTION public.l2_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$l2_distance$function$
;

-- DROP FUNCTION public.l2_norm(sparsevec);

CREATE OR REPLACE FUNCTION public.l2_norm(sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_norm$function$
;

-- DROP FUNCTION public.l2_norm(halfvec);

CREATE OR REPLACE FUNCTION public.l2_norm(halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_norm$function$
;

-- DROP FUNCTION public.l2_normalize(vector);

CREATE OR REPLACE FUNCTION public.l2_normalize(vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$l2_normalize$function$
;

-- DROP FUNCTION public.l2_normalize(sparsevec);

CREATE OR REPLACE FUNCTION public.l2_normalize(sparsevec)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_normalize$function$
;

-- DROP FUNCTION public.l2_normalize(halfvec);

CREATE OR REPLACE FUNCTION public.l2_normalize(halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_normalize$function$
;

-- DROP FUNCTION public.sparsevec(sparsevec, int4, bool);

CREATE OR REPLACE FUNCTION public.sparsevec(sparsevec, integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec$function$
;

-- DROP FUNCTION public.sparsevec_cmp(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_cmp(sparsevec, sparsevec)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_cmp$function$
;

-- DROP FUNCTION public.sparsevec_eq(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_eq(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_eq$function$
;

-- DROP FUNCTION public.sparsevec_ge(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_ge(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_ge$function$
;

-- DROP FUNCTION public.sparsevec_gt(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_gt(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_gt$function$
;

-- DROP FUNCTION public.sparsevec_in(cstring, oid, int4);

CREATE OR REPLACE FUNCTION public.sparsevec_in(cstring, oid, integer)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_in$function$
;

-- DROP FUNCTION public.sparsevec_l2_squared_distance(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_l2_squared_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_squared_distance$function$
;

-- DROP FUNCTION public.sparsevec_le(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_le(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_le$function$
;

-- DROP FUNCTION public.sparsevec_lt(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_lt(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_lt$function$
;

-- DROP FUNCTION public.sparsevec_ne(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_ne(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_ne$function$
;

-- DROP FUNCTION public.sparsevec_negative_inner_product(sparsevec, sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_negative_inner_product(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_negative_inner_product$function$
;

-- DROP FUNCTION public.sparsevec_out(sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_out(sparsevec)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_out$function$
;

-- DROP FUNCTION public.sparsevec_recv(internal, oid, int4);

CREATE OR REPLACE FUNCTION public.sparsevec_recv(internal, oid, integer)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_recv$function$
;

-- DROP FUNCTION public.sparsevec_send(sparsevec);

CREATE OR REPLACE FUNCTION public.sparsevec_send(sparsevec)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_send$function$
;

-- DROP FUNCTION public.sparsevec_to_halfvec(sparsevec, int4, bool);

CREATE OR REPLACE FUNCTION public.sparsevec_to_halfvec(sparsevec, integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_to_halfvec$function$
;

-- DROP FUNCTION public.sparsevec_to_vector(sparsevec, int4, bool);

CREATE OR REPLACE FUNCTION public.sparsevec_to_vector(sparsevec, integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_to_vector$function$
;

-- DROP FUNCTION public.sparsevec_typmod_in(_cstring);

CREATE OR REPLACE FUNCTION public.sparsevec_typmod_in(cstring[])
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_typmod_in$function$
;

-- DROP FUNCTION public.subvector(vector, int4, int4);

CREATE OR REPLACE FUNCTION public.subvector(vector, integer, integer)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$subvector$function$
;

-- DROP FUNCTION public.subvector(halfvec, int4, int4);

CREATE OR REPLACE FUNCTION public.subvector(halfvec, integer, integer)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_subvector$function$
;

-- DROP AGGREGATE public.sum(vector);

-- Aggregate function public.sum(vector)
-- ERROR: more than one function named "public.sum";

-- DROP AGGREGATE public.sum(halfvec);

-- Aggregate function public.sum(halfvec)
-- ERROR: more than one function named "public.sum";

-- DROP FUNCTION public.vector(vector, int4, bool);

CREATE OR REPLACE FUNCTION public.vector(vector, integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector$function$
;

-- DROP FUNCTION public.vector_accum(_float8, vector);

CREATE OR REPLACE FUNCTION public.vector_accum(double precision[], vector)
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_accum$function$
;

-- DROP FUNCTION public.vector_add(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_add(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_add$function$
;

-- DROP FUNCTION public.vector_avg(_float8);

CREATE OR REPLACE FUNCTION public.vector_avg(double precision[])
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_avg$function$
;

-- DROP FUNCTION public.vector_cmp(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_cmp(vector, vector)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_cmp$function$
;

-- DROP FUNCTION public.vector_combine(_float8, _float8);

CREATE OR REPLACE FUNCTION public.vector_combine(double precision[], double precision[])
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_combine$function$
;

-- DROP FUNCTION public.vector_concat(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_concat(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_concat$function$
;

-- DROP FUNCTION public.vector_dims(vector);

CREATE OR REPLACE FUNCTION public.vector_dims(vector)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_dims$function$
;

-- DROP FUNCTION public.vector_dims(halfvec);

CREATE OR REPLACE FUNCTION public.vector_dims(halfvec)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_vector_dims$function$
;

-- DROP FUNCTION public.vector_eq(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_eq(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_eq$function$
;

-- DROP FUNCTION public.vector_ge(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_ge(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_ge$function$
;

-- DROP FUNCTION public.vector_gt(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_gt(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_gt$function$
;

-- DROP FUNCTION public.vector_in(cstring, oid, int4);

CREATE OR REPLACE FUNCTION public.vector_in(cstring, oid, integer)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_in$function$
;

-- DROP FUNCTION public.vector_l2_squared_distance(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_l2_squared_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_l2_squared_distance$function$
;

-- DROP FUNCTION public.vector_le(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_le(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_le$function$
;

-- DROP FUNCTION public.vector_lt(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_lt(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_lt$function$
;

-- DROP FUNCTION public.vector_mul(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_mul(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_mul$function$
;

-- DROP FUNCTION public.vector_ne(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_ne(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_ne$function$
;

-- DROP FUNCTION public.vector_negative_inner_product(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_negative_inner_product(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_negative_inner_product$function$
;

-- DROP FUNCTION public.vector_norm(vector);

CREATE OR REPLACE FUNCTION public.vector_norm(vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_norm$function$
;

-- DROP FUNCTION public.vector_out(vector);

CREATE OR REPLACE FUNCTION public.vector_out(vector)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_out$function$
;

-- DROP FUNCTION public.vector_recv(internal, oid, int4);

CREATE OR REPLACE FUNCTION public.vector_recv(internal, oid, integer)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_recv$function$
;

-- DROP FUNCTION public.vector_send(vector);

CREATE OR REPLACE FUNCTION public.vector_send(vector)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_send$function$
;

-- DROP FUNCTION public.vector_spherical_distance(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_spherical_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_spherical_distance$function$
;

-- DROP FUNCTION public.vector_sub(vector, vector);

CREATE OR REPLACE FUNCTION public.vector_sub(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_sub$function$
;

-- DROP FUNCTION public.vector_to_float4(vector, int4, bool);

CREATE OR REPLACE FUNCTION public.vector_to_float4(vector, integer, boolean)
 RETURNS real[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_to_float4$function$
;

-- DROP FUNCTION public.vector_to_halfvec(vector, int4, bool);

CREATE OR REPLACE FUNCTION public.vector_to_halfvec(vector, integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_to_halfvec$function$
;

-- DROP FUNCTION public.vector_to_sparsevec(vector, int4, bool);

CREATE OR REPLACE FUNCTION public.vector_to_sparsevec(vector, integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_to_sparsevec$function$
;

-- DROP FUNCTION public.vector_typmod_in(_cstring);

CREATE OR REPLACE FUNCTION public.vector_typmod_in(cstring[])
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_typmod_in$function$
;