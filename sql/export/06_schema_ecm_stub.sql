-- The ecm_stub schema: the stand-in ECM and PGP index. DDL only;
-- data follows in the 07_* files.
--
-- PostgreSQL database dump
--

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.7 (Homebrew)

--
-- Name: ecm_stub; Type: SCHEMA; Schema: -; Owner: -
--

DROP SCHEMA IF EXISTS ecm_stub CASCADE;

CREATE SCHEMA ecm_stub;

--
-- Name: ecm_document; Type: TABLE; Schema: ecm_stub; Owner: -
--

CREATE TABLE ecm_stub.ecm_document (
    doc_id text NOT NULL,
    kb_id text NOT NULL,
    title text NOT NULL,
    path text NOT NULL,
    space text NOT NULL,
    owner text NOT NULL,
    labels text[] DEFAULT '{}'::text[] NOT NULL,
    sensitivity text DEFAULT 'internal'::text NOT NULL,
    acl_principals text[] DEFAULT '{}'::text[] NOT NULL,
    version text,
    updated_at timestamp with time zone,
    mime text DEFAULT 'text/html'::text NOT NULL,
    body bytea NOT NULL,
    forbidden_for text[] DEFAULT '{}'::text[] NOT NULL,
    canonical_of text
);

--
-- Name: pgp_index_entry; Type: TABLE; Schema: ecm_stub; Owner: -
--

CREATE TABLE ecm_stub.pgp_index_entry (
    doc_id text NOT NULL,
    kb_id text NOT NULL,
    raw_metadata jsonb NOT NULL,
    facets jsonb DEFAULT '{}'::jsonb NOT NULL,
    stale boolean DEFAULT false NOT NULL,
    embedding extensions.vector(1536),
    fragment text
);

--
-- Name: ecm_document ecm_document_pkey; Type: CONSTRAINT; Schema: ecm_stub; Owner: -
--

ALTER TABLE ONLY ecm_stub.ecm_document
    ADD CONSTRAINT ecm_document_pkey PRIMARY KEY (doc_id);

--
-- Name: pgp_index_entry pgp_index_entry_pkey; Type: CONSTRAINT; Schema: ecm_stub; Owner: -
--

ALTER TABLE ONLY ecm_stub.pgp_index_entry
    ADD CONSTRAINT pgp_index_entry_pkey PRIMARY KEY (kb_id, doc_id);

--
-- Name: ix_ecm_document_fts; Type: INDEX; Schema: ecm_stub; Owner: -
--

CREATE INDEX ix_ecm_document_fts ON ecm_stub.ecm_document USING gin (to_tsvector('english'::regconfig, ((title || ' '::text) || path)));

--
-- Name: ix_ecm_document_kb; Type: INDEX; Schema: ecm_stub; Owner: -
--

CREATE INDEX ix_ecm_document_kb ON ecm_stub.ecm_document USING btree (kb_id);

--
-- Name: ix_ecm_document_space; Type: INDEX; Schema: ecm_stub; Owner: -
--

CREATE INDEX ix_ecm_document_space ON ecm_stub.ecm_document USING btree (space);

--
-- Name: ix_pgp_embedding; Type: INDEX; Schema: ecm_stub; Owner: -
--

CREATE INDEX ix_pgp_embedding ON ecm_stub.pgp_index_entry USING hnsw (embedding extensions.vector_cosine_ops);

--
-- Name: ix_pgp_stale; Type: INDEX; Schema: ecm_stub; Owner: -
--

CREATE INDEX ix_pgp_stale ON ecm_stub.pgp_index_entry USING btree (kb_id) WHERE stale;

--
-- Name: ix_pgp_url; Type: INDEX; Schema: ecm_stub; Owner: -
--

CREATE INDEX ix_pgp_url ON ecm_stub.pgp_index_entry USING btree (((facets ->> 'url'::text)));

--
-- PostgreSQL database dump complete
--


