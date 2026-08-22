# askcontent-backend

The API and worker for **askcontent** — grounded question answering over a
bounded set of company content, where every factual sentence carries a citation
to the span that supports it.

One of three repositories. They share no code; they share **contracts** — the
HTTP surface and the SSE event envelope — and nothing else.

| Repository | Contents |
|---|---|
| **`askcontent-backend`** (this one) | API, worker, ports and adapters, evaluation harness, tests, design and engineering docs |
| `askcontent-console` | The React administration console |
| `askcontent-widget` | The embeddable widget and its React wrapper |
| `askcontent-sample-data` | Seeded corpora, the stand-in source schema, loaders, planted effects |

**Why the split.** Different deploy cadences, different reviewers, and the
console must build in an environment with no Python toolchain at all
(`ARC-REP-01`).

---

## The one decision everything else follows from

> **The model never writes a retrieval query, and never chooses its own scope.**

The model fills a typed `RetrievalSpec` whose `scope_ref` is an **identifier of
a reviewed scope object**, not a string the model composes. The server compiles
that to a source query plus the caller's access predicate.

The failure this guards is **scope leakage** — an answer built on a document
from another business group, or on one the asker cannot open. Because scope is
a reference and not text, *widening it is unrepresentable*.

---

## The retrieval topology, which is not one system

| System | What it is | What it returns |
|---|---|---|
| **PGP** | The company-wide vector index | **Document identifiers** and scores — not content |
| **ECM** | The content manager: the system of record | The document itself, plus authoritative metadata |

A question runs in two stages: **find identifiers**, then **go and get the
documents**. Everything hard in this product lives in the gap between them.

The consequence that surprises people: because the index hands back identifiers
rather than passages, **passages are recovered locally** — fetch, parse, chunk,
select. So the parsing subsystem is on the critical path of every answer, not a
feature of the custom-ingestion path.

---

## Run it

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e .
PYTHONPATH=src .venv/bin/python -m uvicorn askcontent.api.app:app --port 8000
PYTHONPATH=src .venv/bin/python -m pytest -q     # 37 tests, fully offline
```

The whole suite runs with no network, no database, no API key and no model
download.

### Optional extras

| Extra | Adds | Without it |
|---|---|---|
| `pdf` | pypdfium2 + Docling | PDFs **refuse with a reason** rather than parsing badly (`CNT-PAR-11`) |
| `ocr` | RapidOCR | The scan rung of the ladder is unavailable and reports so |
| `rerank` | sentence-transformers | The deterministic lexical reranker is used; set `ASKCONTENT_RERANKER=cross-encoder` once installed |

`GET /api/health` reports exactly which capabilities this deployment has. A
capability gap must be visible as a gap, never discovered as a mysteriously
empty corpus.

---

## The database

SQLAlchemy models with Alembic on a linear revision history — the same shape as
askdb, because an engineer who knows one should know the other.

```bash
PYTHONPATH=src .venv/bin/python -m alembic upgrade head
PYTHONPATH=src .venv/bin/python -m alembic upgrade head --sql   # review first
```

**27 tenant tables, 4 control-plane tables, 24 row-level security policies.**
Table groups mirror `PLT-DM-*`; where a group differs it is because content
differs from a schema, and the model's docstring says how.

| Group | Tables |
|---|---|
| Identity and tenancy | `org`, `app_user`, `membership`, `workspace`, `auth_session` |
| Sources | `knowledgebase`, `connector`, `field_rule` |
| The catalog | `document`, `document_chunk`, `document_pin`, `authority_rule` |
| Vectors | `embedding` |
| Plans and terms | `retrieval_plan`, `glossary_term` |
| Conversations | `thread`, `message` |
| Audit | `retrieval_run`, `scope_change` |
| RBAC | `rbac_role`, `rbac_role_member`, `rbac_label_rule`, `rbac_policy_version` |
| Operations | `job`, `quarantine_item`, `embed`, `embed_session` |
| Control plane *(separate database)* | `tenant`, `tenant_migration`, `global_user`, `user_tenant` |

### Four things here are load-bearing

**Revision 0001 creates the schema *and* the policies together** (`PLT-DM-18`).
Splitting them leaves a window in which the tables exist and the policies do
not, and that window is exactly when someone runs the first data load.

**The DDL is generated from the ORM metadata**, by `tools/render_ddl.py` and
`tools/render_rls.py`, into `migrations/sql/`. Regenerate and diff rather than
hand-editing — a table added without a policy is the kind of omission that stays
invisible until it is a breach.

**The tenant setting is transaction-local.** `set_config('askcontent.org_id', …,
true)`, never session-scoped. Behind a transaction-mode pooler a session-scoped
setting belongs to whichever request last used that backend, which is a
cross-tenant read — and it passes every test that uses a single connection.

**The application role must not own the tables and must not hold `BYPASSRLS`**
(`ARC-TEC-04`), or every policy above is silently inert. Creating that role is a
deployment step, not a migration, because a migration cannot safely create roles
on a managed platform. `scripts/provision_supabase.sh` does it.

### The vector index trap

`PLT-VEC-08` — the index and the query must use the **same distance expression
and the same width cast**, produced by one shared helper
(`src/askcontent/db/vector_ops.py`). The HNSW index type caps vector width below
the widest embedding models, so the index is built on a narrowed cast. If the
query expression differs, the database **silently falls back to a sequential
scan**. It is not an error; it is a hundredfold latency regression that presents
as "search feels slow".

### Where it runs

The **shared Supabase project** that already hosts askdb
(`qeiayokzacpmxvthrpdp`, us-east-2). askdb owns the `askdb` schema; we own
`askcontent`. Neither touches `public`, and `askdb`'s 36 tables are untouched.

```bash
cp .env.example .env      # then fill in, or copy the DSN from askdb-backend/.env
set -a && . ./.env && set +a
PYTHONPATH=src .venv/bin/python -m alembic upgrade head
```

Current state: **revision 0002**, 27 tables, 24 policies, HNSW + two GIN
indexes, and the `people-ops` corpus loaded into `ecm_stub` (125 documents in
the store, 128 in the index — the 3 unresolvable ones are a planted effect).

### Four things this deployment taught us the hard way

**The direct host does not resolve.** `db.<ref>.supabase.co` is IPv6-only.
Both URLs point at the **pooler**, and the region prefix is `aws-1`, not
`aws-0` — `aws-0-us-east-2` resolves in DNS but answers
`tenant/user not found`, which reads like a credential problem and is not.

| Port | Mode | Use |
|---|---|---|
| 5432 | session | migrations, `CREATE EXTENSION`, DDL |
| 6543 | transaction | the application, if you want it |

**`options` on the DSN is silently dropped.** Supavisor discards the startup
parameter, so `?options=-csearch_path=...` yields a working connection whose
search_path is the server default — and 27 tables land in `public` on a shared
database. `src/askcontent/db/schema.py` issues `SET search_path` from a connect
listener instead, which a session-mode pooler cannot drop. `statement_timeout`
is set there for the same reason.

**pgvector lives in `extensions`, not `public`.** That schema must be on the
search_path or an unqualified `VECTOR(...)` in the DDL fails to resolve.

**The version table is prefixed.** `alembic_version` is the default name for
*every* project using Alembic, so on a shared database it is the one table
guaranteed to collide — silently and catastrophically, with two services
reading each other's revision pointer. Ours is
`askcontent.askcontent_alembic_version`; askdb's is `askdb.askdb_alembic_version`.

### The control plane is a separate tree

`migrations_control/` with its own `alembic_control.ini` and its own history:

```bash
alembic -c alembic_control.ini upgrade head    # only when multi-tenant
```

Not tidiness. A single tree means one `alembic upgrade head` applies
control-plane tables to every tenant database it touches, and the separation in
`PLT-TEN-01/02` exists precisely so that cannot happen. This deployment is
single-tenant (`PLT-TEN-03`), so it is never run.

### Known gap

`ARC-TEC-04` wants the application to connect as a **non-owning role without
`BYPASSRLS`**. On Supabase's pooler, tenant routing is keyed on
`postgres.<project-ref>`, so a custom role needs pooler support that is not
configured here — the application currently connects as `postgres`, which owns
the tables. The policies are created and correct, but they are **not being
exercised** by this connection. Fix before any second tenant exists; it is a
deployment change, not a code change.

---

## Layout

```
docs/design/        what to build — the normative specification
docs/engineering/   what was built, what broke, what was measured, and why
src/askcontent/
  domain/           pure: scope, chunks, catalog, the RetrievalSpec grammar, ids
  ports/            ContentIndex, ContentRepository, Parser, Embedder, Reranker
  adapters/         the only place a vendor library is imported
  services/         mapping, registry, probe, passages, retrieval
  api/              FastAPI surface
  fixtures/         the seed corpus the mocks serve
tests/
```

`ContentIndex` and `ContentRepository` are **two ports on purpose**
(`CNT-FED-01`). PGP finds things and holds no content; the ECM holds things and
ranks nothing. A single combined port would hide the most important fact about
this architecture: **a hit is not a document.**

---

## What is real and what is mocked

Neither PGP nor the ECM is available to build against, so both sit behind ports
with **behaviourally detailed mocks**.

| Real | Mocked |
|---|---|
| Scope evaluation and the two gates | PGP (`adapters/index/mock_pgp.py`) |
| Field mapping, coercion, validation | ECM (`adapters/repository/mock_ecm.py`) |
| HTML parsing, chunking, heading paths | Embeddings (deterministic hashing) |
| The full six-stage pipeline and its trace | Cross-encoder weights (lexical stand-in) |
| Conflict detection, staleness, authority | |
| The probe, the audit trail | |

Both mocks carry a header comment naming **the real call that replaces each
method**, the assumed request and response shapes, and the open questions to
settle with that system's owners (`CNT-FED-04`). Start there.

They are deliberately unpleasant: long-tailed latency, cursor pagination,
identifiers PGP holds that the ECM has deleted, documents the index returns
that a principal may not read, metadata that disagrees between the two systems,
documents with no version field, and injectable failure. A mock that always
succeeds in a millisecond and agrees with itself lets a design through that
cannot survive either real system.

---

## The five questions to settle before writing a real adapter

Each changes the architecture, not just the adapter.

1. **Does PGP accept metadata filters in the query, or only kNN?** If only kNN,
   the scope predicate cannot be pushed down, and `CNT-SCP-14` forces a local
   metadata store keyed by PGP document id.
2. **Does PGP enforce ACLs per calling user?** If it returns everything the
   service credential can see, the resolution gate in `services/retrieval.py`
   is the only thing preventing a leak. The mock is built this way on purpose,
   so a regression there fails a test rather than a customer.
3. **Can the ECM accept delegated end-user identity?** If not, we store
   resolved principal sets and `CNT-ACL-05`'s revocation interval becomes our
   SLA rather than theirs.
4. **Does the ECM expose a version or etag?** Without one the passage cache
   saves parsing but not fetching, and it is the largest latency lever here.
5. **Is there a batch access-check endpoint?** Per-document authorization across
   a 40-candidate fan-out will otherwise dominate the latency budget.

---

## Operations — the notes that must not be discovered in production

- **The worker is not optional.** Ingest, re-index and quarantine review all run
  there.
- **The streaming endpoint needs an unbuffered proxy.** A proxy that thinks it
  is helping turns a streamed answer into a long silence followed by a wall of
  text.
- **Publish static egress addresses.** Enterprise customers will need to
  allowlist them, and that constraint shapes the network design — decide it
  before the first customer asks.
- **The application database role must not own the tables and must not bypass
  row-level security**, or the tenant policies are silently inert.
- **Model weights are baked into the image, not fetched at boot.** A reranker
  that downloads on first request fails on the day the egress rules change, and
  it fails as a latency spike rather than as an error.
- **Never point a connector at a knowledgebase that has not passed check ④ of
  the probe.** It will answer nothing and report no error.

---

## Documentation

Two sets, with different jobs (`ARC-REP-10`):

| Set | Job |
|---|---|
| [`docs/design/`](docs/design/) | **What to build.** Written up front; remains the reference for intended behaviour |
| [`docs/engineering/`](docs/engineering/) | **What was built**, what broke, what was measured, and why each decision went the way it did |

| Design document | Contents |
|---|---|
| [`00-overview-and-non-goals.md`](docs/design/00-overview-and-non-goals.md) | The product, the personas, what this is explicitly not |
| [`01-connectors-and-knowledge-scope.md`](docs/design/01-connectors-and-knowledge-scope.md) | **The containment model.** Connectors, knowledge scope, two-gate enforcement, sensitivity ceilings, revocation |
| [`02-ingestion-and-parsing.md`](docs/design/02-ingestion-and-parsing.md) | The parser port, HTML and PDF, the fallback ladder, hashing, the sandbox, chunking |
| [`03-federation-and-retrieval.md`](docs/design/03-federation-and-retrieval.md) | **The two-stage pipeline.** Index and repository ports, candidate generation, resolution, passage recovery, the cross-encoder reranker, citations |
| [`04-corpus-catalog.md`](docs/design/04-corpus-catalog.md) | Classification, authority tiers, freshness, human review that survives re-ingest |
| [`06-evaluation-gate.md`](docs/design/06-evaluation-gate.md) | What replaces askdb's determinism gate |

The console's screens are specified in `askcontent-console`, and the widget in
`askcontent-widget`. The HTTP contract lives **inside the console repository**
(`ARC-REP-02`) so a console developer never needs this repository checked out.

---

## Non-negotiables

1. The model never composes a retrieval query and never names its own scope.
2. Knowledge scope is chosen **before** ingestion and re-checked **at
   retrieval**. Two gates, because one is a leak.
3. Scope **narrowing** takes effect immediately; only widening waits for a job.
4. Access predicates are compiled into the retrieval query, never post-filtered.
5. Document classification is a **pure function** of metadata and structure —
   no model call, ever.
6. No answer may cite a document the asker cannot open.
7. A claim without a supporting span is **not emitted**.
8. Two authoritative sources that disagree are both shown, with dates and owners.
9. Human corrections to the catalog survive **every** future ingest run.
10. Parsers run sandboxed; a malformed document fails itself and nothing else.
11. Cache the **plan and the evidence set**, never the prose.
12. Every ingest, scope change and retrieval writes an audit row, whether or not
    it succeeded.
