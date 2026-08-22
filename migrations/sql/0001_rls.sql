-- Row-level security. Generated from the ORM metadata by
-- tools/render_rls.py; regenerate rather than hand-edit.
--
-- PLT-DM-02 / PLT-TEN-18 — every tenant-scoped table carries an organisation
-- identifier and a policy that reads a *transaction-local* setting. Defence in
-- depth: the application already filters by organisation; the policy is there
-- for when it does not.
--
-- ARC-TEC-06 — the setting is transaction-local (`set_config(..., true)`), never
-- session-scoped. Behind a transaction-mode pooler a session setting belongs to
-- whichever request last used that backend, which is a cross-tenant read.
--
-- `auth_session` is deliberately absent (PLT-DM-03): a policy there would have to
-- be satisfied before the session could be read in order to satisfy it.

CREATE OR REPLACE FUNCTION askcontent.current_org() RETURNS uuid AS $$
  SELECT nullif(current_setting('askcontent.org_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

ALTER TABLE askcontent.knowledgebase ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.knowledgebase FORCE ROW LEVEL SECURITY;
CREATE POLICY knowledgebase_tenant_isolation ON askcontent.knowledgebase
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.membership ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.membership FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_tenant_isolation ON askcontent.membership
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.workspace ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.workspace FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_tenant_isolation ON askcontent.workspace
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.connector ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.connector FORCE ROW LEVEL SECURITY;
CREATE POLICY connector_tenant_isolation ON askcontent.connector
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.authority_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.authority_rule FORCE ROW LEVEL SECURITY;
CREATE POLICY authority_rule_tenant_isolation ON askcontent.authority_rule
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.document ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.document FORCE ROW LEVEL SECURITY;
CREATE POLICY document_tenant_isolation ON askcontent.document
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.document_pin ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.document_pin FORCE ROW LEVEL SECURITY;
CREATE POLICY document_pin_tenant_isolation ON askcontent.document_pin
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.embed ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.embed FORCE ROW LEVEL SECURITY;
CREATE POLICY embed_tenant_isolation ON askcontent.embed
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.embedding ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.embedding FORCE ROW LEVEL SECURITY;
CREATE POLICY embedding_tenant_isolation ON askcontent.embedding
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.field_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.field_rule FORCE ROW LEVEL SECURITY;
CREATE POLICY field_rule_tenant_isolation ON askcontent.field_rule
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.glossary_term ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.glossary_term FORCE ROW LEVEL SECURITY;
CREATE POLICY glossary_term_tenant_isolation ON askcontent.glossary_term
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.job ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.job FORCE ROW LEVEL SECURITY;
CREATE POLICY job_tenant_isolation ON askcontent.job
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.quarantine_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.quarantine_item FORCE ROW LEVEL SECURITY;
CREATE POLICY quarantine_item_tenant_isolation ON askcontent.quarantine_item
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.rbac_policy_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.rbac_policy_version FORCE ROW LEVEL SECURITY;
CREATE POLICY rbac_policy_version_tenant_isolation ON askcontent.rbac_policy_version
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.rbac_role ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.rbac_role FORCE ROW LEVEL SECURITY;
CREATE POLICY rbac_role_tenant_isolation ON askcontent.rbac_role
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.retrieval_plan ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.retrieval_plan FORCE ROW LEVEL SECURITY;
CREATE POLICY retrieval_plan_tenant_isolation ON askcontent.retrieval_plan
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.retrieval_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.retrieval_run FORCE ROW LEVEL SECURITY;
CREATE POLICY retrieval_run_tenant_isolation ON askcontent.retrieval_run
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.scope_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.scope_change FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_change_tenant_isolation ON askcontent.scope_change
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.thread ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.thread FORCE ROW LEVEL SECURITY;
CREATE POLICY thread_tenant_isolation ON askcontent.thread
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.document_chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.document_chunk FORCE ROW LEVEL SECURITY;
CREATE POLICY document_chunk_tenant_isolation ON askcontent.document_chunk
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.embed_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.embed_session FORCE ROW LEVEL SECURITY;
CREATE POLICY embed_session_tenant_isolation ON askcontent.embed_session
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.message ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.message FORCE ROW LEVEL SECURITY;
CREATE POLICY message_tenant_isolation ON askcontent.message
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.rbac_label_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.rbac_label_rule FORCE ROW LEVEL SECURITY;
CREATE POLICY rbac_label_rule_tenant_isolation ON askcontent.rbac_label_rule
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());

ALTER TABLE askcontent.rbac_role_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.rbac_role_member FORCE ROW LEVEL SECURITY;
CREATE POLICY rbac_role_member_tenant_isolation ON askcontent.rbac_role_member
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());
