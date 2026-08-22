#!/usr/bin/env bash
#
# End-to-end provisioning against a Supabase project.
#
# Prerequisites, in order, and each is checked below:
#   1. SUPABASE_ACCESS_TOKEN  — a personal access token from
#      https://supabase.com/dashboard/account/tokens
#   2. A project. This script creates one if SUPABASE_PROJECT_REF is unset.
#   3. SUPABASE_DB_PASSWORD   — the database password for that project.
#
# What it does:
#   · creates the project (or uses the one you name)
#   · runs `alembic upgrade head` through the DIRECT connection
#   · creates the non-owning application role
#   · loads a sample dataset into the ecm_stub schema
#   · verifies: extension present, 24 policies, revision at head
#
set -euo pipefail

PROJECT_NAME="${SUPABASE_PROJECT_NAME:-askcontent}"
REGION="${SUPABASE_REGION:-us-east-1}"
DATASET="${DATASET:-people-ops}"
SAMPLES_DIR="${SAMPLES_DIR:-../askcontent-sample-data}"
SUPABASE="npx --yes supabase@latest"

die() { echo "error: $*" >&2; exit 1; }

[[ -n "${SUPABASE_ACCESS_TOKEN:-}" ]] || die \
  "SUPABASE_ACCESS_TOKEN is not set. Create one at
   https://supabase.com/dashboard/account/tokens then:
     export SUPABASE_ACCESS_TOKEN=sbp_..."

[[ -n "${SUPABASE_DB_PASSWORD:-}" ]] || die \
  "SUPABASE_DB_PASSWORD is not set. Choose a strong password; it is the
   database password for the project."

# -- 1. project ---------------------------------------------------------------
if [[ -z "${SUPABASE_PROJECT_REF:-}" ]]; then
  echo "==> creating project '$PROJECT_NAME' in $REGION"
  $SUPABASE projects create "$PROJECT_NAME" \
    --region "$REGION" \
    --db-password "$SUPABASE_DB_PASSWORD" \
    --org-id "${SUPABASE_ORG_ID:?set SUPABASE_ORG_ID — list them with: npx supabase orgs list}"
  SUPABASE_PROJECT_REF="$($SUPABASE projects list --output json \
    | python3 -c "import json,sys;print(next(p['id'] for p in json.load(sys.stdin) if p['name']=='$PROJECT_NAME'))")"
  echo "==> project ref: $SUPABASE_PROJECT_REF"
  echo "==> waiting for the project to come up"
  sleep 45
fi

HOST="db.${SUPABASE_PROJECT_REF}.supabase.co"
ENC_PW="$(python3 -c "import urllib.parse,os;print(urllib.parse.quote(os.environ['SUPABASE_DB_PASSWORD'],safe=''))")"

# Port 5432 is the DIRECT connection. Port 6543 is pgbouncer in transaction
# mode: DDL, advisory locks and CREATE EXTENSION do not survive it, so
# migrations must never run through 6543.
export ASKCONTENT_MIGRATION_DATABASE_URL="postgresql+psycopg://postgres:${ENC_PW}@${HOST}:5432/postgres"
export ASKCONTENT_DATABASE_URL="postgresql+psycopg://postgres.${SUPABASE_PROJECT_REF}:${ENC_PW}@aws-0-${REGION}.pooler.supabase.com:6543/postgres"
PSQL_DSN="postgresql://postgres:${ENC_PW}@${HOST}:5432/postgres"

# -- 2. migrations ------------------------------------------------------------
echo "==> alembic upgrade head (direct connection, port 5432)"
PYTHONPATH=src .venv/bin/python -m alembic upgrade head

# -- 3. the application role --------------------------------------------------
# ARC-TEC-04 — the application role must not own the tables and must not hold
# BYPASSRLS, or every policy is silently inert. A migration cannot safely create
# roles on a managed platform, so it happens here and revision 0001 grants to it
# if it exists. Re-run the migration after this the first time.
echo "==> creating the non-owning application role"
psql "$PSQL_DSN" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'askcontent_app') THEN
    EXECUTE format('CREATE ROLE askcontent_app LOGIN PASSWORD %L', current_setting('askcontent.app_password', true));
  END IF;
END $$;
SQL
PYTHONPATH=src .venv/bin/python -m alembic upgrade head

# -- 4. sample data -----------------------------------------------------------
if [[ -d "$SAMPLES_DIR" ]]; then
  echo "==> loading sample dataset '$DATASET' into ecm_stub"
  (cd "$SAMPLES_DIR" && \
    .venv/bin/python -m askcontent_samples.cli generate "$DATASET" && \
    .venv/bin/python -m askcontent_samples.cli load "$DATASET" "$PSQL_DSN")
else
  echo "==> skipping sample data: $SAMPLES_DIR not found"
fi

# -- 5. verify ----------------------------------------------------------------
echo "==> verifying"
psql "$PSQL_DSN" -v ON_ERROR_STOP=1 <<'SQL'
\echo '-- pgvector'
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
\echo '-- alembic revision'
SELECT version_num FROM askcontent.alembic_version;
\echo '-- tables'
SELECT count(*) AS tables FROM information_schema.tables WHERE table_schema = 'askcontent';
\echo '-- row-level security policies (expect 24)'
SELECT count(*) AS policies FROM pg_policies WHERE schemaname = 'askcontent';
\echo '-- tables with RLS enabled but no policy (expect none)'
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'askcontent' AND c.relrowsecurity
  AND NOT EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname='askcontent' AND p.tablename=c.relname);
\echo '-- sample source'
SELECT count(*) AS ecm_documents FROM ecm_stub.ecm_document;
SELECT count(*) AS index_entries, count(*) FILTER (WHERE stale) AS stale FROM ecm_stub.pgp_index_entry;
SQL

cat <<MSG

Done.

  export ASKCONTENT_DATABASE_URL='$ASKCONTENT_DATABASE_URL'
  export ASKCONTENT_MIGRATION_DATABASE_URL='$ASKCONTENT_MIGRATION_DATABASE_URL'

Put these in .env (which is gitignored). Note the two different ports: the
application uses the pooler on 6543, migrations use the direct connection on
5432.
MSG
