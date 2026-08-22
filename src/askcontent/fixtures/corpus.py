"""Seed corpus for the mock PGP index and mock ECM repository.

WHAT THIS STANDS IN FOR
-----------------------
Real deployments read this material from two systems we do not have access to.
The corpus below is shaped to reproduce the properties that actually make the
design hard, rather than a tidy set that any pipeline would handle:

  * Five knowledgebases with **five different metadata field vocabularies**.
    This is the single most important property here. It is why the field map
    (CNT-MAP-*) exists and why per-knowledgebase code branches are prohibited.
  * Documents whose index metadata **disagrees** with the store's.
  * Documents present in the index that the store no longer has (stale ids).
  * Documents the index returns that a given principal may not read.
  * Documents with no version field, and documents with no parseable date.
  * A restricted knowledgebase above the default sensitivity ceiling.
  * Near-duplicate documents across two spaces, one canonical.
  * Two authoritative documents that contradict each other.

Replace with: a fixture export taken from the real systems once access exists,
keeping every property above represented.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

NOW = dt.datetime(2026, 8, 22, 12, 0, 0)


def _days_ago(n: int) -> dt.datetime:
    return NOW - dt.timedelta(days=n)


@dataclass
class SeedDoc:
    doc_id: str
    kb_id: str
    title: str
    path: str
    space: str
    body_html: str
    updated_at: dt.datetime | None
    owner: str
    labels: tuple[str, ...] = ()
    sensitivity: str = "internal"
    acl_principals: tuple[str, ...] = ("group:all-staff",)
    version: str | None = "1"
    mime: str = "text/html"
    # Simulated store behaviour -------------------------------------------
    missing_from_store: bool = False   # indexed, then deleted at source
    forbidden_for: tuple[str, ...] = ()
    canonical_of: str | None = None    # this doc is a duplicate of that one
    # Index/store metadata disagreement: the index's stale copy of the title.
    index_title_override: str | None = None
    extras: dict[str, str] = field(default_factory=dict)


def _page(*sections: tuple[str, str]) -> str:
    parts = ["<html><body>"]
    for heading, body in sections:
        parts.append(f"<h2>{heading}</h2>")
        parts.append(body)
    parts.append("</body></html>")
    return "\n".join(parts)


SEED: list[SeedDoc] = [
    # ---------------------------------------------------------------- HR ---
    SeedDoc(
        doc_id="HR-1041",
        kb_id="kb-hr-policies",
        title="Parental Leave Policy",
        path="/hr/policies/parental-leave",
        space="HR",
        owner="dana.okafor@example.com",
        labels=("policy", "benefits", "approved"),
        updated_at=_days_ago(45),
        body_html=_page(
            (
                "Purpose",
                "<p>This policy sets out paid and unpaid parental leave for all "
                "permanent employees. Policy owner: People Operations. "
                "Effective date: 1 March 2026. Approved by: the Remuneration "
                "Committee.</p>",
            ),
            (
                "Entitlement",
                "<p>A primary caregiver is entitled to <b>18 weeks</b> of paid "
                "parental leave. A secondary caregiver is entitled to 6 weeks "
                "of paid leave. Both entitlements are available in the 24 "
                "months following birth or placement.</p>"
                "<table><tr><th>Caregiver</th><th>Paid weeks</th>"
                "<th>Unpaid weeks</th></tr>"
                "<tr><td>Primary</td><td>18</td><td>34</td></tr>"
                "<tr><td>Secondary</td><td>6</td><td>12</td></tr></table>",
            ),
            (
                "Applying",
                "<p>Submit form PL-1 through the People portal at least 10 "
                "weeks before the intended start date. Late applications are "
                "considered but cannot be guaranteed.</p>",
            ),
        ),
    ),
    SeedDoc(
        doc_id="HR-0887",
        kb_id="kb-hr-policies",
        title="Parental Leave — Guidance for Managers",
        path="/hr/guidance/parental-leave-managers",
        space="HR",
        owner="dana.okafor@example.com",
        labels=("guidance",),
        updated_at=_days_ago(400),
        # Contradicts HR-1041 on the headline number. Surfaced, never resolved
        # silently (CNT-RET-20, CNT-CAT-06).
        body_html=_page(
            (
                "Overview",
                "<p>Primary caregivers receive <b>12 weeks</b> of paid parental "
                "leave. Managers should plan cover accordingly.</p>",
            ),
            (
                "Cover planning",
                "<p>Agree a handover plan at least 8 weeks before the leave "
                "begins and record it in the team plan.</p>",
            ),
        ),
    ),
    SeedDoc(
        doc_id="HR-2210",
        kb_id="kb-hr-policies",
        title="Expenses and Reimbursement Policy",
        path="/hr/policies/expenses",
        space="HR",
        owner="finance-ops@example.com",
        labels=("policy", "finance", "approved"),
        updated_at=_days_ago(120),
        body_html=_page(
            (
                "Scope",
                "<p>Applies to all business expenses incurred by employees and "
                "contractors. Policy owner: Finance Operations.</p>",
            ),
            (
                "Limits",
                "<table><tr><th>Category</th><th>Limit</th><th>Approval</th></tr>"
                "<tr><td>Meals (domestic)</td><td>45 per day</td><td>Line manager</td></tr>"
                "<tr><td>Meals (international)</td><td>70 per day</td><td>Line manager</td></tr>"
                "<tr><td>Air travel</td><td>Economy under 6 hours</td><td>Director</td></tr>"
                "<tr><td>Accommodation</td><td>220 per night</td><td>Line manager</td></tr>"
                "</table>",
            ),
            (
                "Submission deadline",
                "<p>Claims must be submitted within <b>60 days</b> of the "
                "expense date. Claims outside this window require Director "
                "approval and a written explanation.</p>",
            ),
        ),
    ),
    SeedDoc(
        doc_id="HR-0455",
        kb_id="kb-hr-policies",
        title="Expenses Policy (superseded 2024)",
        path="/hr/archive/expenses-2024",
        space="HR",
        owner="finance-ops@example.com",
        labels=("archive", "superseded"),
        updated_at=_days_ago(1200),
        body_html=_page(
            ("Limits", "<p>Meals are limited to 30 per day. Claims must be "
             "submitted within 30 days.</p>"),
        ),
    ),
    SeedDoc(
        doc_id="HR-3001",
        kb_id="kb-hr-policies",
        title="Remote Working Standard",
        path="/hr/policies/remote-working",
        space="HR",
        owner="dana.okafor@example.com",
        labels=("policy", "approved"),
        updated_at=None,  # no parseable date -> unknown_age (CNT-CAT-10)
        version=None,     # no version field -> CNT-RET-11 fallback
        body_html=_page(
            ("Eligibility", "<p>All roles are eligible for hybrid working "
             "unless the role requires on-site presence.</p>"),
            ("Core hours", "<p>Core collaboration hours are 10:00 to 16:00 in "
             "the employee's registered time zone.</p>"),
        ),
    ),

    # ----------------------------------------------------------- Runbooks ---
    SeedDoc(
        doc_id="RB-114",
        kb_id="kb-eng-runbooks",
        title="Runbook: API rate limit exhaustion",
        path="/eng/runbooks/api-rate-limits",
        space="ENG",
        owner="platform-oncall@example.com",
        labels=("runbook", "oncall", "sev2"),
        updated_at=_days_ago(20),
        body_html=_page(
            ("1. Detect", "<p>Alert <code>API_429_RATE</code> fires when the "
             "429 ratio exceeds 2% over five minutes.</p>"),
            ("2. Triage", "<p>Check the per-tenant limiter dashboard. Confirm "
             "whether one tenant or all tenants are affected.</p>"),
            ("3. Mitigate", "<p>Raise the tenant's burst allowance via "
             "<code>ratectl raise --tenant &lt;id&gt; --burst 2x</code>. This "
             "is reversible and requires no deploy.</p>"),
            ("4. Escalate", "<p>If the limiter itself is unhealthy, page the "
             "Platform on-call rotation.</p>"),
        ),
    ),
    SeedDoc(
        doc_id="RB-115",
        kb_id="kb-eng-runbooks",
        title="Runbook: Search index sync lag",
        path="/eng/runbooks/index-sync-lag",
        space="ENG",
        owner="search-team@example.com",
        labels=("runbook", "oncall"),
        updated_at=_days_ago(8),
        body_html=_page(
            ("1. Detect", "<p>Sync lag above 30 minutes raises "
             "<code>IDX_SYNC_LAG</code>.</p>"),
            ("2. Diagnose", "<p>Inspect the connector's checkpoint. A stalled "
             "checkpoint usually means a poison document.</p>"),
            ("3. Mitigate", "<p>Skip the poison document with "
             "<code>idxctl skip --doc &lt;id&gt;</code> and file a parse "
             "defect.</p>"),
        ),
    ),
    SeedDoc(
        doc_id="RB-090",
        kb_id="kb-eng-runbooks",
        title="Decision: adopt reciprocal rank fusion for multi-channel search",
        path="/eng/decisions/adr-0090-rrf",
        space="ENG",
        owner="search-team@example.com",
        labels=("adr", "decision"),
        updated_at=_days_ago(210),
        body_html=_page(
            ("Status", "<p>Status: Accepted</p>"),
            ("Context", "<p>Context: Vector and keyword channels return scores "
             "on incomparable scales. Merging them numerically produced an "
             "ordering that looked principled and was arbitrary.</p>"),
            ("Decision", "<p>Decision: fuse by rank using reciprocal rank "
             "fusion, and delegate cross-channel comparison to a cross-encoder "
             "reranker that reads the text.</p>"),
            ("Consequences", "<p>Consequences: the reranker becomes a required "
             "stage rather than an optimisation.</p>"),
        ),
    ),
    SeedDoc(
        doc_id="RB-201",
        kb_id="kb-eng-runbooks",
        title="Deprecated: legacy queue runbook",
        path="/eng/archive/legacy-queue",
        space="ENG",
        owner="platform-oncall@example.com",
        labels=("archive",),
        updated_at=_days_ago(1400),
        body_html=_page(("Note", "<p>This system was decommissioned.</p>")),
    ),
    SeedDoc(
        doc_id="RB-777",
        kb_id="kb-eng-runbooks",
        title="Runbook: certificate rotation",
        path="/eng/runbooks/cert-rotation",
        space="ENG",
        owner="security-eng@example.com",
        labels=("runbook",),
        updated_at=_days_ago(60),
        # Indexed by PGP, since deleted at the source. Exercises `stale_index`
        # (CNT-RET-06), the most common production surprise.
        missing_from_store=True,
        body_html=_page(("Steps", "<p>Rotate the intermediate certificate.</p>")),
    ),

    # ------------------------------------------------------------ Finance ---
    SeedDoc(
        doc_id="FIN-3300",
        kb_id="kb-fin-controls",
        title="Revenue Recognition Control Narrative",
        path="/finance/controls/rev-rec",
        space="FIN",
        owner="controller@example.com",
        labels=("control", "sox"),
        sensitivity="confidential",
        acl_principals=("group:finance", "group:audit"),
        updated_at=_days_ago(90),
        forbidden_for=("user:asha", "group:all-staff"),
        body_html=_page(
            ("Control objective", "<p>Revenue is recognised in the period in "
             "which the performance obligation is satisfied.</p>"),
            ("Key controls", "<p>Monthly reconciliation performed by the "
             "Controller and reviewed by the CFO.</p>"),
        ),
    ),
    SeedDoc(
        doc_id="FIN-3301",
        kb_id="kb-fin-controls",
        title="Refund Policy",
        path="/finance/policies/refunds",
        space="FIN",
        owner="controller@example.com",
        labels=("policy", "approved", "customer-facing"),
        updated_at=_days_ago(30),
        body_html=_page(
            ("Eligibility", "<p>Customers may request a full refund within "
             "<b>30 days</b> of purchase. Annual plans are refundable pro rata "
             "after 30 days.</p>"),
            ("Exclusions", "<p>Usage-based charges already incurred are not "
             "refundable. Professional services are non-refundable once "
             "delivery has begun.</p>"),
            ("Processing", "<p>Approved refunds are processed within 10 "
             "business days to the original payment method.</p>"),
        ),
    ),

    # --------------------------------------------------------------- Legal --
    SeedDoc(
        doc_id="LGL-9001",
        kb_id="kb-legal-holds",
        title="Litigation Hold — Matter 2026-114",
        path="/legal/holds/2026-114",
        space="LEGAL",
        owner="general-counsel@example.com",
        labels=("legal-hold", "restricted"),
        sensitivity="restricted",
        acl_principals=("group:legal",),
        updated_at=_days_ago(15),
        forbidden_for=("group:all-staff", "user:asha", "user:rob"),
        body_html=_page(("Instruction", "<p>Preserve all documents relating to "
                         "matter 2026-114.</p>")),
    ),

    # ------------------------------------------------------------ Web/dupes -
    SeedDoc(
        doc_id="WEB-500",
        kb_id="kb-marketing-web",
        title="Refund Policy",
        path="/www/legal/refund-policy",
        space="WEB",
        owner="marketing@example.com",
        labels=("public", "customer-facing"),
        sensitivity="public",
        updated_at=_days_ago(200),
        # Near-duplicate of FIN-3301 and *staler*. Without cross-source dedup
        # the answer cites this one and diverges from the system of record
        # (CNT-PAR-21).
        canonical_of="FIN-3301",
        body_html=_page(
            ("Refunds", "<p>Customers may request a full refund within 30 days "
             "of purchase. Annual plans are refundable pro rata after 30 "
             "days.</p>"),
        ),
    ),
    SeedDoc(
        doc_id="WEB-501",
        kb_id="kb-marketing-web",
        title="Security and Compliance Overview",
        path="/www/trust/security",
        space="WEB",
        owner="marketing@example.com",
        labels=("public",),
        sensitivity="public",
        updated_at=_days_ago(75),
        # The index's copy of the title is stale — exercises CNT-RET-08.
        index_title_override="Security Overview (draft)",
        body_html=_page(
            ("Certifications", "<p>We maintain SOC 2 Type II and ISO 27001 "
             "certification, audited annually.</p>"),
            ("Data residency", "<p>Customer data is stored in the region "
             "selected at provisioning and is not replicated across "
             "regions.</p>"),
            ("Frequently asked", "<p>Where is my data stored?</p>"),
        ),
    ),
]

SEED_BY_ID: dict[str, SeedDoc] = {d.doc_id: d for d in SEED}


# ---------------------------------------------------------------------------
# Per-knowledgebase metadata vocabularies.
#
# This is the crux of the admin-configuration problem: the same canonical
# concept has a different field name, and sometimes a different *shape*, in
# every knowledgebase. The platform never learns these names — the field map
# does (CNT-MAP-01), and it is data.
# ---------------------------------------------------------------------------

KB_FIELD_VOCABULARY: dict[str, dict[str, str]] = {
    "kb-hr-policies": {
        "doc_id": "documentNumber",
        "title": "docTitle",
        "url": "portalUrl",
        "updated_at": "lastModified",     # ISO-8601 string
        "space": "spaceKey",
        "owner": "ownerEmail",
        "labels": "tags",                 # comma-separated string
        "sensitivity": "classification",
        "acl_principals": "readGroups",
    },
    "kb-eng-runbooks": {
        "doc_id": "id",
        "title": "name",
        "url": "link",
        "updated_at": "updated",          # epoch seconds
        "space": "team",
        "owner": "maintainer",
        "labels": "topics",               # list
        "sensitivity": "level",
        "acl_principals": "acl",
    },
    "kb-fin-controls": {
        "doc_id": "controlDocId",
        "title": "heading",
        "url": "href",
        "updated_at": "revisedOn",        # DD/MM/YYYY
        "space": "domain",
        "owner": "accountableParty",
        "labels": "keywords",
        "sensitivity": "infoClass",
        "acl_principals": "entitlements",
    },
    "kb-legal-holds": {
        "doc_id": "matterDocId",
        "title": "subject",
        "url": "uri",
        "updated_at": "issuedOn",
        "space": "practiceArea",
        "owner": "custodian",
        "labels": "flags",
        "sensitivity": "handling",
        "acl_principals": "permittedGroups",
    },
    "kb-marketing-web": {
        "doc_id": "pageId",
        "title": "pageTitle",
        "url": "canonicalUrl",
        "updated_at": "publishedAt",
        "space": "site",
        "owner": "contentOwner",
        "labels": "categories",
        # Deliberately absent: this knowledgebase exposes no ACL fields at all,
        # which forces the explicit access-class declaration of CNT-ACL-03.
    },
}

KB_DESCRIPTIONS: dict[str, tuple[str, str, bool]] = {
    # kb_id: (display name, description, exposes_acl)
    "kb-hr-policies": ("HR Policies", "People Operations policy library", True),
    "kb-eng-runbooks": ("Engineering Runbooks", "On-call runbooks and decision records", True),
    "kb-fin-controls": ("Finance Controls", "SOX control narratives and finance policy", True),
    "kb-legal-holds": ("Legal Holds", "Litigation holds and matter instructions", True),
    "kb-marketing-web": ("Public Web", "Published marketing and trust pages", False),
}
