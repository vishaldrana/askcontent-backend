-- ===== TENANT PLANE =====
CREATE TABLE askcontent.app_user (
	email VARCHAR(320) NOT NULL, 
	display_name VARCHAR(200), 
	password_hash TEXT, 
	is_active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_app_user PRIMARY KEY (id), 
	CONSTRAINT uq_app_user_email UNIQUE (email)
);

CREATE TABLE askcontent.org (
	slug VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_org PRIMARY KEY (id), 
	CONSTRAINT uq_org_slug UNIQUE (slug)
);

CREATE TABLE askcontent.auth_session (
	token_hash VARCHAR(128) NOT NULL, 
	user_id UUID NOT NULL, 
	org_id UUID, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_auth_session PRIMARY KEY (id), 
	CONSTRAINT uq_auth_session_token_hash UNIQUE (token_hash), 
	CONSTRAINT fk_auth_session_user_id FOREIGN KEY(user_id) REFERENCES askcontent.app_user (id) ON DELETE CASCADE
);

CREATE TABLE askcontent.knowledgebase (
	kb_id VARCHAR(200) NOT NULL, 
	name VARCHAR(300) NOT NULL, 
	description TEXT, 
	document_count INTEGER NOT NULL, 
	last_indexed_at TIMESTAMP WITH TIME ZONE, 
	embedding_model VARCHAR(200), 
	embedding_dimension INTEGER, 
	exposes_acl BOOLEAN NOT NULL, 
	observed_fields JSONB NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_knowledgebase PRIMARY KEY (id), 
	CONSTRAINT uq_knowledgebase_org_id UNIQUE (org_id, kb_id), 
	CONSTRAINT fk_knowledgebase_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_knowledgebase_org_id ON askcontent.knowledgebase (org_id);

CREATE TABLE askcontent.membership (
	user_id UUID NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_membership PRIMARY KEY (id), 
	CONSTRAINT uq_membership_org_id UNIQUE (org_id, user_id), 
	CONSTRAINT fk_membership_user_id FOREIGN KEY(user_id) REFERENCES askcontent.app_user (id) ON DELETE CASCADE, 
	CONSTRAINT fk_membership_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_membership_org_id ON askcontent.membership (org_id);

CREATE TABLE askcontent.workspace (
	slug VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_workspace PRIMARY KEY (id), 
	CONSTRAINT uq_workspace_org_id UNIQUE (org_id, slug), 
	CONSTRAINT fk_workspace_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_workspace_org_id ON askcontent.workspace (org_id);

CREATE TABLE askcontent.connector (
	slug VARCHAR(64) NOT NULL, 
	name VARCHAR(300) NOT NULL, 
	workspace_id UUID NOT NULL, 
	knowledgebase_id UUID NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	scope JSONB NOT NULL, 
	scope_hash VARCHAR(64) NOT NULL, 
	sensitivity_ceiling VARCHAR(16) NOT NULL, 
	access_groups TEXT[] NOT NULL, 
	declared_access_class VARCHAR(200), 
	retrieval_config JSONB NOT NULL, 
	catalog_version INTEGER NOT NULL, 
	policy_version INTEGER NOT NULL, 
	credential_ciphertext BYTEA, 
	credential_key_id VARCHAR(64), 
	last_ingest_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_connector PRIMARY KEY (id), 
	CONSTRAINT uq_connector_org_id UNIQUE (org_id, slug), 
	CONSTRAINT ck_connector_state CHECK (state in ('draft','active','suspended')), 
	CONSTRAINT fk_connector_workspace_id FOREIGN KEY(workspace_id) REFERENCES askcontent.workspace (id) ON DELETE CASCADE, 
	CONSTRAINT fk_connector_knowledgebase_id FOREIGN KEY(knowledgebase_id) REFERENCES askcontent.knowledgebase (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_connector_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_connector_org_id ON askcontent.connector (org_id);

CREATE TABLE askcontent.authority_rule (
	connector_id UUID NOT NULL, 
	ordinal INTEGER NOT NULL, 
	space VARCHAR(200), 
	path_prefix TEXT, 
	label VARCHAR(200), 
	tier VARCHAR(16) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_authority_rule PRIMARY KEY (id), 
	CONSTRAINT fk_authority_rule_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_authority_rule_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_authority_rule_org_id ON askcontent.authority_rule (org_id);

CREATE TABLE askcontent.document (
	connector_id UUID NOT NULL, 
	doc_id VARCHAR(300) NOT NULL, 
	title TEXT NOT NULL, 
	url TEXT NOT NULL, 
	path TEXT, 
	space VARCHAR(200), 
	owner VARCHAR(320), 
	labels TEXT[] NOT NULL, 
	source_version VARCHAR(200), 
	source_updated_at TIMESTAMP WITH TIME ZONE, 
	sensitivity VARCHAR(16) NOT NULL, 
	acl_principals TEXT[] NOT NULL, 
	extras JSONB NOT NULL, 
	mime VARCHAR(128), 
	size_bytes BIGINT, 
	file_hash VARCHAR(64), 
	text_hash VARCHAR(64), 
	parser_id VARCHAR(64), 
	parser_version VARCHAR(32), 
	parse_path VARCHAR(32), 
	parse_quality JSONB NOT NULL, 
	refusal_reason TEXT, 
	doc_type VARCHAR(32), 
	doc_type_confidence FLOAT, 
	doc_type_source VARCHAR(16), 
	doc_type_evidence TEXT[] NOT NULL, 
	authority VARCHAR(16) NOT NULL, 
	authority_reason VARCHAR(64), 
	staleness VARCHAR(16) NOT NULL, 
	in_scope BOOLEAN NOT NULL, 
	exclusion_rule VARCHAR(64), 
	exclusion_detail TEXT, 
	quarantined BOOLEAN NOT NULL, 
	superseded_by VARCHAR(300), 
	canonical_doc_id VARCHAR(300), 
	missing_since TIMESTAMP WITH TIME ZONE, 
	last_seen_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_document PRIMARY KEY (id), 
	CONSTRAINT uq_document_connector_id UNIQUE (connector_id, doc_id), 
	CONSTRAINT fk_document_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_document_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_document_space ON askcontent.document (connector_id, space);
CREATE INDEX ix_document_scope ON askcontent.document (connector_id, in_scope);
CREATE INDEX ix_document_updated ON askcontent.document (connector_id, source_updated_at);
CREATE INDEX ix_askcontent_document_org_id ON askcontent.document (org_id);

CREATE TABLE askcontent.document_pin (
	connector_id UUID NOT NULL, 
	doc_id VARCHAR(300) NOT NULL, 
	field VARCHAR(32) NOT NULL, 
	value TEXT NOT NULL, 
	actor VARCHAR(320) NOT NULL, 
	note TEXT, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_document_pin PRIMARY KEY (id), 
	CONSTRAINT uq_document_pin_connector_id UNIQUE (connector_id, doc_id, field), 
	CONSTRAINT fk_document_pin_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_document_pin_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_document_pin_org_id ON askcontent.document_pin (org_id);

CREATE TABLE askcontent.embed (
	connector_id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	publishable_key VARCHAR(128) NOT NULL, 
	allowed_origins TEXT[] NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_embed PRIMARY KEY (id), 
	CONSTRAINT uq_embed_publishable_key UNIQUE (publishable_key), 
	CONSTRAINT fk_embed_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_embed_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_embed_org_id ON askcontent.embed (org_id);

CREATE TABLE askcontent.embedding (
	connector_id UUID NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	ref_id VARCHAR(300) NOT NULL, 
	parent_ref VARCHAR(300), 
	content_hash VARCHAR(64) NOT NULL, 
	model_id VARCHAR(200) NOT NULL, 
	dimension INTEGER NOT NULL, 
	vector VECTOR(1536) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_embedding PRIMARY KEY (id), 
	CONSTRAINT uq_embedding_connector_id UNIQUE (connector_id, kind, ref_id, model_id), 
	CONSTRAINT fk_embedding_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_embedding_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_embedding_org_id ON askcontent.embedding (org_id);
CREATE INDEX ix_embedding_lookup ON askcontent.embedding (connector_id, kind);

CREATE TABLE askcontent.field_rule (
	connector_id UUID NOT NULL, 
	target VARCHAR(64) NOT NULL, 
	source VARCHAR(200), 
	coercion VARCHAR(32) NOT NULL, 
	value_map JSONB NOT NULL, 
	default_value TEXT, 
	prefer VARCHAR(8) NOT NULL, 
	observed_coverage FLOAT, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_field_rule PRIMARY KEY (id), 
	CONSTRAINT uq_field_rule_connector_id UNIQUE (connector_id, target), 
	CONSTRAINT fk_field_rule_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_field_rule_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_field_rule_org_id ON askcontent.field_rule (org_id);

CREATE TABLE askcontent.glossary_term (
	connector_id UUID NOT NULL, 
	term VARCHAR(200) NOT NULL, 
	definition TEXT NOT NULL, 
	aliases TEXT[] NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_glossary_term PRIMARY KEY (id), 
	CONSTRAINT uq_glossary_term_connector_id UNIQUE (connector_id, term), 
	CONSTRAINT fk_glossary_term_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_glossary_term_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_glossary_term_org_id ON askcontent.glossary_term (org_id);

CREATE TABLE askcontent.job (
	connector_id UUID, 
	kind VARCHAR(32) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	progress JSONB NOT NULL, 
	error TEXT, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_job PRIMARY KEY (id), 
	CONSTRAINT fk_job_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_job_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_job_queue ON askcontent.job (status, created_at);
CREATE INDEX ix_askcontent_job_org_id ON askcontent.job (org_id);

CREATE TABLE askcontent.quarantine_item (
	connector_id UUID NOT NULL, 
	doc_id VARCHAR(300) NOT NULL, 
	matched_class VARCHAR(64) NOT NULL, 
	redacted_span TEXT, 
	status VARCHAR(16) NOT NULL, 
	reviewed_by VARCHAR(320), 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_quarantine_item PRIMARY KEY (id), 
	CONSTRAINT fk_quarantine_item_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_quarantine_item_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_quarantine_item_org_id ON askcontent.quarantine_item (org_id);

CREATE TABLE askcontent.rbac_policy_version (
	connector_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	changed_by VARCHAR(320), 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_rbac_policy_version PRIMARY KEY (id), 
	CONSTRAINT fk_rbac_policy_version_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_rbac_policy_version_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_rbac_policy_version_org_id ON askcontent.rbac_policy_version (org_id);

CREATE TABLE askcontent.rbac_role (
	connector_id UUID NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_rbac_role PRIMARY KEY (id), 
	CONSTRAINT uq_rbac_role_connector_id UNIQUE (connector_id, name), 
	CONSTRAINT fk_rbac_role_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_rbac_role_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_rbac_role_org_id ON askcontent.rbac_role (org_id);

CREATE TABLE askcontent.retrieval_plan (
	connector_id UUID NOT NULL, 
	question_hash VARCHAR(64) NOT NULL, 
	question TEXT NOT NULL, 
	plan_hash VARCHAR(64) NOT NULL, 
	spec JSONB NOT NULL, 
	evidence_chunk_ids TEXT[] NOT NULL, 
	catalog_version INTEGER NOT NULL, 
	reranker_id VARCHAR(64) NOT NULL, 
	hit_count INTEGER NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_retrieval_plan PRIMARY KEY (id), 
	CONSTRAINT uq_retrieval_plan_connector_id UNIQUE (connector_id, question_hash, catalog_version, reranker_id), 
	CONSTRAINT fk_retrieval_plan_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_retrieval_plan_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_retrieval_plan_org_id ON askcontent.retrieval_plan (org_id);

CREATE TABLE askcontent.retrieval_run (
	connector_id UUID NOT NULL, 
	actor VARCHAR(320) NOT NULL, 
	question TEXT NOT NULL, 
	spec JSONB NOT NULL, 
	plan_hash VARCHAR(64) NOT NULL, 
	returned_doc_ids TEXT[] NOT NULL, 
	refused_doc_ids TEXT[] NOT NULL, 
	stale_index_count INTEGER NOT NULL, 
	forbidden_count INTEGER NOT NULL, 
	degraded TEXT[] NOT NULL, 
	answered BOOLEAN NOT NULL, 
	refusal_reason TEXT, 
	duration_ms FLOAT, 
	cache_hit_rate FLOAT, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_retrieval_run PRIMARY KEY (id), 
	CONSTRAINT fk_retrieval_run_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_retrieval_run_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_retrieval_run_org_id ON askcontent.retrieval_run (org_id);
CREATE INDEX ix_run_connector_time ON askcontent.retrieval_run (connector_id, created_at);

CREATE TABLE askcontent.scope_change (
	connector_id UUID NOT NULL, 
	actor VARCHAR(320) NOT NULL, 
	scope_before JSONB, 
	scope_after JSONB NOT NULL, 
	added INTEGER NOT NULL, 
	removed INTEGER NOT NULL, 
	unchanged INTEGER NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_scope_change PRIMARY KEY (id), 
	CONSTRAINT fk_scope_change_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_scope_change_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_scope_change_org_id ON askcontent.scope_change (org_id);

CREATE TABLE askcontent.thread (
	workspace_id UUID NOT NULL, 
	connector_id UUID NOT NULL, 
	user_id UUID, 
	title TEXT, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_thread PRIMARY KEY (id), 
	CONSTRAINT fk_thread_workspace_id FOREIGN KEY(workspace_id) REFERENCES askcontent.workspace (id) ON DELETE CASCADE, 
	CONSTRAINT fk_thread_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_thread_user_id FOREIGN KEY(user_id) REFERENCES askcontent.app_user (id) ON DELETE SET NULL, 
	CONSTRAINT fk_thread_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_thread_org_id ON askcontent.thread (org_id);

CREATE TABLE askcontent.document_chunk (
	connector_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	chunk_id VARCHAR(64) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	text TEXT NOT NULL, 
	heading_path TEXT[] NOT NULL, 
	parent_text TEXT, 
	page INTEGER, 
	is_table BOOLEAN NOT NULL, 
	token_estimate INTEGER, 
	chunker_version VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_document_chunk PRIMARY KEY (id), 
	CONSTRAINT uq_document_chunk_document_id UNIQUE (document_id, ordinal), 
	CONSTRAINT fk_document_chunk_connector_id FOREIGN KEY(connector_id) REFERENCES askcontent.connector (id) ON DELETE CASCADE, 
	CONSTRAINT fk_document_chunk_document_id FOREIGN KEY(document_id) REFERENCES askcontent.document (id) ON DELETE CASCADE, 
	CONSTRAINT fk_document_chunk_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_chunk_chunk_id ON askcontent.document_chunk (connector_id, chunk_id);
CREATE INDEX ix_askcontent_document_chunk_org_id ON askcontent.document_chunk (org_id);

CREATE TABLE askcontent.embed_session (
	embed_id UUID NOT NULL, 
	visitor_id VARCHAR(320) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_embed_session PRIMARY KEY (id), 
	CONSTRAINT fk_embed_session_embed_id FOREIGN KEY(embed_id) REFERENCES askcontent.embed (id) ON DELETE CASCADE, 
	CONSTRAINT fk_embed_session_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_embed_session_org_id ON askcontent.embed_session (org_id);

CREATE TABLE askcontent.message (
	thread_id UUID NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	text TEXT NOT NULL, 
	sidecar JSONB NOT NULL, 
	refused BOOLEAN NOT NULL, 
	refusal_reason TEXT, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_message PRIMARY KEY (id), 
	CONSTRAINT fk_message_thread_id FOREIGN KEY(thread_id) REFERENCES askcontent.thread (id) ON DELETE CASCADE, 
	CONSTRAINT fk_message_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_message_thread ON askcontent.message (thread_id, created_at);
CREATE INDEX ix_askcontent_message_org_id ON askcontent.message (org_id);

CREATE TABLE askcontent.rbac_label_rule (
	role_id UUID NOT NULL, 
	space VARCHAR(200), 
	label VARCHAR(200), 
	effect VARCHAR(8) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_rbac_label_rule PRIMARY KEY (id), 
	CONSTRAINT fk_rbac_label_rule_role_id FOREIGN KEY(role_id) REFERENCES askcontent.rbac_role (id) ON DELETE CASCADE, 
	CONSTRAINT fk_rbac_label_rule_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_rbac_label_rule_org_id ON askcontent.rbac_label_rule (org_id);

CREATE TABLE askcontent.rbac_role_member (
	role_id UUID NOT NULL, 
	principal VARCHAR(320) NOT NULL, 
	id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_rbac_role_member PRIMARY KEY (id), 
	CONSTRAINT uq_rbac_role_member_role_id UNIQUE (role_id, principal), 
	CONSTRAINT fk_rbac_role_member_role_id FOREIGN KEY(role_id) REFERENCES askcontent.rbac_role (id) ON DELETE CASCADE, 
	CONSTRAINT fk_rbac_role_member_org_id FOREIGN KEY(org_id) REFERENCES askcontent.org (id) ON DELETE CASCADE
);
CREATE INDEX ix_askcontent_rbac_role_member_org_id ON askcontent.rbac_role_member (org_id);

-- ===== CONTROL PLANE =====
CREATE TABLE askcontent_control.global_user (
	email VARCHAR(320) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_global_user PRIMARY KEY (id), 
	CONSTRAINT uq_global_user_email UNIQUE (email)
);

CREATE TABLE askcontent_control.tenant (
	slug VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	region VARCHAR(32) NOT NULL, 
	cluster VARCHAR(64), 
	sealed_dsn BYTEA NOT NULL, 
	revision VARCHAR(64), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_tenant PRIMARY KEY (id), 
	CONSTRAINT uq_tenant_slug UNIQUE (slug)
);

CREATE TABLE askcontent_control.tenant_migration (
	tenant_id UUID NOT NULL, 
	revision VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	error TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_tenant_migration PRIMARY KEY (id), 
	CONSTRAINT fk_tenant_migration_tenant_id FOREIGN KEY(tenant_id) REFERENCES askcontent_control.tenant (id) ON DELETE CASCADE
);

CREATE TABLE askcontent_control.user_tenant (
	global_user_id UUID NOT NULL, 
	tenant_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_user_tenant PRIMARY KEY (id), 
	CONSTRAINT uq_user_tenant_global_user_id UNIQUE (global_user_id, tenant_id), 
	CONSTRAINT fk_user_tenant_global_user_id FOREIGN KEY(global_user_id) REFERENCES askcontent_control.global_user (id) ON DELETE CASCADE, 
	CONSTRAINT fk_user_tenant_tenant_id FOREIGN KEY(tenant_id) REFERENCES askcontent_control.tenant (id) ON DELETE CASCADE
);

