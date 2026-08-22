"""Regenerate migrations/sql/0001_rls.sql from the ORM metadata.

Committed so the policy set is reproducible: a reviewer regenerates and diffs
rather than reading 160 lines of SQL and hoping every table is covered. A table
added without a policy is the kind of omission that is invisible until it is a
breach.

    PYTHONPATH=src python tools/render_rls.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, "src")

from askcontent.db.models import TENANT_TABLES  # noqa: E402

HEADER = """-- Row-level security. Generated from the ORM metadata by
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
"""


def main() -> None:
    parts = [HEADER]
    for table in TENANT_TABLES:
        parts.append(
            f"""
ALTER TABLE askcontent.{table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE askcontent.{table} FORCE ROW LEVEL SECURITY;
CREATE POLICY {table}_tenant_isolation ON askcontent.{table}
  USING (org_id = askcontent.current_org())
  WITH CHECK (org_id = askcontent.current_org());
"""
        )
    pathlib.Path("migrations/sql/0001_rls.sql").write_text("".join(parts))
    print(f"wrote policies for {len(TENANT_TABLES)} tables")


if __name__ == "__main__":
    main()
