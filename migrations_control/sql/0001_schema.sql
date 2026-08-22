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

