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

Generated from `askcontent-sample-data` (the `retail-bank` dataset, HTML
documents only). Regenerate rather than hand-editing: the sample-data repo is
where the corpus is designed, and this is the offline smoke copy of it.

PDF documents are deliberately excluded — a committed fixture does not need to
carry binary, and the PDF paths are exercised against a loaded database.
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
    SeedDoc(
        doc_id='CB-POL-1041',
        kb_id='kb-consumer-policy',
        title='Consumer Deposit Account Fee Schedule',
        path='/consumer/policies/deposit-fee-schedule',
        space='CONSUMER',
        owner='deposit.products@wellsfargo.example',
        labels=('policy', 'approved', 'customer-facing', 'reg-dd'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-07-15T12:00:00+00:00'),
        missing_from_store=False,
        body_html='<html><body>\n<h2>Purpose</h2>\n<p>This schedule sets the fees applied to consumer deposit accounts. It is the controlling document for all channels: branch, contact centre, online and mobile. Policy owner: Deposit Products. Effective date: 1 July 2026. Approved by: the Consumer Banking Pricing Committee.</p>\n<h2>Overdraft and returned item fees</h2>\n<p>An overdraft fee of <b>$35</b> is assessed per item when an item is paid into overdraft. No more than <b>three</b> overdraft fees are assessed per business day. No overdraft fee is assessed when the account is overdrawn by <b>$5 or less</b> at the end of the business day.</p><table><tr><th>Fee</th><th>Amount</th><th>Daily cap</th></tr><tr><td>Overdraft (item paid)</td><td>$35</td><td>3 per day</td></tr><tr><td>Returned item (item declined)</td><td>$0</td><td>—</td></tr><tr><td>Overdraft protection transfer</td><td>$12.50</td><td>1 per day</td></tr></table>\n<h2>Monthly service fee</h2>\n<p>The monthly service fee for Everyday Checking is $10 and is waived when any one of the following applies during the statement period: $500 or more in total qualifying direct deposits, a $500 minimum daily balance, or a primary account owner aged 17 to 24.</p>\n<h2>Grace period</h2>\n<p>A customer who brings the account to a non-negative balance by <b>11:59 PM local time on the next business day</b> has overdraft fees for that day reversed automatically.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='CB-GDE-0887',
        kb_id='kb-consumer-policy',
        title='Overdraft Fees — Branch Quick Reference',
        path='/consumer/guidance/overdraft-quick-reference',
        space='CONSUMER',
        owner='branch.enablement@wellsfargo.example',
        labels=('guidance',),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2025-06-18T12:00:00+00:00'),
        missing_from_store=False,
        body_html="<html><body>\n<h2>Overdraft basics</h2>\n<p>An overdraft fee of <b>$15</b> is charged per item, with a maximum of <b>four</b> per day. Tellers should quote these figures when a customer asks at the counter.</p>\n<h2>Waivers</h2>\n<p>A one-time courtesy waiver may be offered once per rolling twelve months at the banker's discretion.</p>\n</body></html>",
    ),
    SeedDoc(
        doc_id='CB-POL-2210',
        kb_id='kb-consumer-policy',
        title='Regulation E Error Resolution Procedure',
        path='/consumer/policies/reg-e-error-resolution',
        space='CONSUMER',
        owner='consumer.compliance@wellsfargo.example',
        labels=('policy', 'approved', 'reg-e', 'disputes'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-05-18T12:00:00+00:00'),
        missing_from_store=False,
        body_html='<html><body>\n<h2>Scope</h2>\n<p>Applies to every notice of error on a consumer account involving an electronic fund transfer, received by any channel. Policy owner: Consumer Compliance.</p>\n<h2>Investigation timeframes</h2>\n<table><tr><th>Condition</th><th>Investigate within</th><th>Provisional credit</th></tr><tr><td>Standard error notice</td><td>10 business days</td><td>Not required if resolved in 10</td></tr><tr><td>Extended investigation</td><td>45 calendar days</td><td>Required by day 10</td></tr><tr><td>New account (first 30 days)</td><td>20 business days</td><td>Required by day 20</td></tr><tr><td>Point-of-sale or foreign-initiated</td><td>90 calendar days</td><td>Required by day 10</td></tr></table>\n<h2>Customer notification</h2>\n<p>The customer must be told the result within <b>three business days</b> of completing the investigation. Where the claim is denied, the notice must state the reason and offer the supporting documents.</p>\n<h2>Notice deadline</h2>\n<p>A customer has <b>60 days</b> from the statement date on which the error first appeared to give notice.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='CB-POL-0455',
        kb_id='kb-consumer-policy',
        title='Consumer Deposit Fee Schedule (superseded 2024)',
        path='/consumer/archive/deposit-fee-schedule-2024',
        space='CONSUMER',
        owner='deposit.products@wellsfargo.example',
        labels=('archive', 'superseded'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2023-04-30T12:00:00+00:00'),
        missing_from_store=False,
        body_html='<html><body>\n<h2>Fees</h2>\n<p>Overdraft fee of $35 per item, up to five per day. Monthly service fee $12.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='CB-POL-3001',
        kb_id='kb-consumer-policy',
        title='Funds Availability Standard (Regulation CC)',
        path='/consumer/policies/funds-availability',
        space='CONSUMER',
        owner='deposit.operations@wellsfargo.example',
        labels=('policy', 'approved', 'reg-cc'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        missing_from_store=False,
        updated_at=None,
        body_html="<html><body>\n<h2>Next-day availability</h2>\n<p>The first $275 of a day's check deposits is available on the next business day.</p>\n<h2>Exception holds</h2>\n<p>A hold may be extended where the account has been repeatedly overdrawn, the deposit exceeds $6,725 in one day, or there is reasonable cause to doubt collectability. The customer must be given a written notice stating which exception applies.</p>\n</body></html>",
    ),
    SeedDoc(
        doc_id='OPS-RB-114',
        kb_id='kb-ops-runbooks',
        title='Runbook: Fedwire cut-off breach',
        path='/ops/runbooks/fedwire-cutoff-breach',
        space='OPS',
        owner='payments.oncall@wellsfargo.example',
        labels=('runbook', 'oncall', 'sev2', 'payments'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-08-05T12:00:00+00:00'),
        missing_from_store=False,
        body_html="<html><body>\n<h2>1. Detect</h2>\n<p>Alert <code>WIRE_CUTOFF_RISK</code> fires when the outbound Fedwire queue still holds value-dated items 15 minutes before the <b>5:00 PM ET</b> customer cut-off.</p>\n<h2>2. Triage</h2>\n<p>Check the queue by originating line of business. Confirm whether the backlog is one large corporate batch or a broad slowdown across channels.</p>\n<h2>3. Mitigate</h2>\n<p>Release held items with <code>wirectl release --queue outbound --priority value-dated</code>. This is reversible and requires no deployment. Items that cannot clear before the Fed's <b>6:00 PM ET</b> closing are value-dated to the next business day.</p>\n<h2>4. Escalate</h2>\n<p>If the Fedwire interface itself is unhealthy, page Payments Engineering on-call and notify the Treasury Services duty manager. A missed cut-off is reportable to Operational Risk within one business day.</p>\n</body></html>",
    ),
    SeedDoc(
        doc_id='OPS-RB-115',
        kb_id='kb-ops-runbooks',
        title='Runbook: Core deposit posting lag',
        path='/ops/runbooks/core-posting-lag',
        space='OPS',
        owner='deposit.oncall@wellsfargo.example',
        labels=('runbook', 'oncall'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-08-13T12:00:00+00:00'),
        missing_from_store=False,
        body_html='<html><body>\n<h2>1. Detect</h2>\n<p>Posting lag above 30 minutes raises <code>CORE_POST_LAG</code>. Balances shown in digital channels are stale for the duration.</p>\n<h2>2. Diagnose</h2>\n<p>Inspect the posting checkpoint. A stalled checkpoint is usually one poison item — most often a memo post with a malformed effective date.</p>\n<h2>3. Mitigate</h2>\n<p>Skip the item with <code>postctl skip --item &lt;id&gt;</code>, then file a break with Deposit Operations the same day. Do not skip more than three items without escalating.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='OPS-ADR-090',
        kb_id='kb-ops-runbooks',
        title='Decision: single source for wire cut-off times',
        path='/ops/decisions/adr-0090-cutoff-source',
        space='OPS',
        owner='payments.architecture@wellsfargo.example',
        labels=('adr', 'decision'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2025-12-25T12:00:00+00:00'),
        missing_from_store=False,
        body_html='<html><body>\n<h2>Status</h2>\n<p>Status: Accepted</p>\n<h2>Context</h2>\n<p>Context: cut-off times were published in four places — the public site, the treasury portal, the branch handbook and the payments service configuration — and they disagreed by up to an hour during daylight-saving transitions.</p>\n<h2>Decision</h2>\n<p>Decision: the payments service configuration is the system of record. Every other surface renders it, and none restates it.</p>\n<h2>Consequences</h2>\n<p>Consequences: a cut-off change is a configuration change with an audit trail, not a documentation change. Surfaces that cannot render live configuration must link rather than copy.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='OPS-RB-201',
        kb_id='kb-ops-runbooks',
        title='Deprecated: legacy ACH exception queue',
        path='/ops/archive/legacy-ach-queue',
        space='OPS',
        owner='payments.oncall@wellsfargo.example',
        labels=('archive',),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2022-10-22T12:00:00+00:00'),
        missing_from_store=False,
        body_html='<html><body>\n<h2>Note</h2>\n<p>This queue was decommissioned when ACH exceptions moved to the unified breaks platform.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='OPS-RB-777',
        kb_id='kb-ops-runbooks',
        title='Runbook: HSM key ceremony',
        path='/ops/runbooks/hsm-key-ceremony',
        space='OPS',
        owner='security.engineering@wellsfargo.example',
        labels=('runbook', 'security'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-06-11T12:00:00+00:00'),
        missing_from_store=True,
        body_html='<html><body>\n<h2>Steps</h2>\n<p>Rotate the payment HSM master key under dual control.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='RSK-CTL-3300',
        kb_id='kb-risk-controls',
        title='BSA/AML Transaction Monitoring Control Narrative',
        path='/risk/controls/bsa-aml-transaction-monitoring',
        space='RISK',
        owner='bsa.officer@wellsfargo.example',
        labels=('control', 'bsa-aml', 'sox'),
        sensitivity='confidential',
        acl_principals=('group:financial-crimes', 'group:audit'),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-05-26T12:00:00+00:00'),
        missing_from_store=False,
        forbidden_for=('user:asha', 'group:all-staff', 'group:consumer-banking'),
        body_html='<html><body>\n<h2>Control objective</h2>\n<p>Transactions are monitored for patterns indicative of money laundering, and alerts are dispositioned within the required timeframe.</p>\n<h2>Alert thresholds</h2>\n<p>Structuring detection triggers on aggregate cash activity between $8,000 and $10,000 across a rolling five-business-day window. Thresholds are model-owned and are not published outside Financial Crimes.</p>\n<h2>Key controls</h2>\n<p>Level 1 review within 30 calendar days of alert generation; Level 2 escalation within 10 further days; SAR filing decision within 30 days of the determination date.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='RSK-POL-3301',
        kb_id='kb-risk-controls',
        title='Wire Transfer Cut-off and Recall Policy',
        path='/risk/policies/wire-cutoff-and-recall',
        space='RISK',
        owner='treasury.services@wellsfargo.example',
        labels=('policy', 'approved', 'customer-facing', 'payments'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-07-27T12:00:00+00:00'),
        missing_from_store=False,
        body_html='<html><body>\n<h2>Cut-off times</h2>\n<p>Domestic wire requests received through online banking are executed the same business day when submitted before <b>5:00 PM ET</b>. International wire requests must be submitted before <b>3:00 PM ET</b>. Requests received after the applicable cut-off are executed on the next business day.</p><table><tr><th>Wire type</th><th>Channel</th><th>Cut-off (ET)</th></tr><tr><td>Domestic</td><td>Online / mobile</td><td>5:00 PM</td></tr><tr><td>Domestic</td><td>Branch</td><td>4:00 PM</td></tr><tr><td>International</td><td>Online / mobile</td><td>3:00 PM</td></tr><tr><td>International</td><td>Branch</td><td>2:00 PM</td></tr></table>\n<h2>Recall</h2>\n<p>A sent wire cannot be cancelled unilaterally. A recall request may be submitted, and the receiving institution is not obliged to return the funds. Recall requests are submitted within <b>one business day</b> where possible.</p>\n<h2>Fees</h2>\n<p>Outgoing domestic wire $30, outgoing international $45, incoming $15. Fees are waived on Premier relationships.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='RSK-CTL-3310',
        kb_id='kb-risk-controls',
        title='Model Risk Management Standard (SR 11-7)',
        path='/risk/controls/model-risk-management',
        space='RISK',
        owner='model.risk@wellsfargo.example',
        labels=('control', 'approved', 'sox'),
        sensitivity='confidential',
        acl_principals=('group:model-risk', 'group:audit'),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-04-04T12:00:00+00:00'),
        missing_from_store=False,
        forbidden_for=('group:all-staff', 'user:asha'),
        body_html='<html><body>\n<h2>Scope</h2>\n<p>Applies to every quantitative method whose output informs a business decision, including vendor models.</p>\n<h2>Validation cadence</h2>\n<p>High-materiality models are validated annually; medium every two years; low every three. A material change triggers validation regardless of cadence.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='LGL-HLD-9001',
        kb_id='kb-legal-holds',
        title='Litigation Hold — Matter 2026-114',
        path='/legal/holds/2026-114',
        space='LEGAL',
        owner='general.counsel@wellsfargo.example',
        labels=('legal-hold', 'restricted'),
        sensitivity='restricted',
        acl_principals=('group:legal',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-08-10T12:00:00+00:00'),
        missing_from_store=False,
        forbidden_for=('group:all-staff', 'user:asha', 'user:rob'),
        body_html='<html><body>\n<h2>Instruction</h2>\n<p>Preserve all documents relating to matter 2026-114. Do not delete, alter or archive.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='WEB-500',
        kb_id='kb-public-web',
        title='Wire Transfers — Fees and Cut-off Times',
        path='/www/help/wire-transfers',
        space='WEB',
        owner='digital.content@wellsfargo.example',
        labels=('published', 'customer-facing'),
        sensitivity='public',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-01-19T12:00:00+00:00'),
        missing_from_store=False,
        canonical_of='RSK-POL-3301',
        body_html='<html><body>\n<h2>Cut-off times</h2>\n<p>Domestic wires submitted online before 5:00 PM ET are sent the same business day. International wires must be submitted before 3:00 PM ET.</p>\n<h2>Fees</h2>\n<p>Outgoing domestic wire $30. Outgoing international $45. Incoming wire $15.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='WEB-501',
        kb_id='kb-public-web',
        title='Security and Fraud Protection',
        path='/www/privacy-security/fraud-protection',
        space='WEB',
        owner='digital.content@wellsfargo.example',
        labels=('published',),
        sensitivity='public',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2026-06-19T12:00:00+00:00'),
        missing_from_store=False,
        index_title_override='Fraud Protection (draft)',
        body_html='<html><body>\n<h2>Zero liability</h2>\n<p>Customers are not held responsible for unauthorised card transactions reported promptly.</p>\n<h2>Reporting a suspected fraud</h2>\n<p>Report suspected fraud immediately through the mobile app, online banking, or by calling the number on the back of the card.</p>\n<h2>Frequently asked</h2>\n<p>How quickly must I report an unauthorised transaction?</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='CB-GONE-00',
        kb_id='kb-consumer-policy',
        title='Withdrawn disclosure bulletin 0',
        path='/consumer/withdrawn/0',
        space='CONSUMER',
        owner='consumer.compliance@wellsfargo.example',
        labels=('guidance',),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        updated_at=dt.datetime.fromisoformat('2025-04-09T12:00:00+00:00'),
        missing_from_store=True,
        body_html='<html><body><p>Withdrawn and replaced.</p></body></html>',
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

KB_FIELD_VOCABULARY: dict[str, dict[str, str]] = {'kb-consumer-policy': {'acl_principals': 'readGroups', 'doc_id': 'documentNumber', 'labels': 'tags', 'owner': 'ownerEmail', 'sensitivity': 'classification', 'space': 'spaceKey', 'title': 'docTitle', 'updated_at': 'lastModified', 'url': 'portalUrl'}, 'kb-ops-runbooks': {'acl_principals': 'acl', 'doc_id': 'id', 'labels': 'topics', 'owner': 'maintainer', 'sensitivity': 'level', 'space': 'team', 'title': 'name', 'updated_at': 'updated', 'url': 'link'}, 'kb-risk-controls': {'acl_principals': 'entitlements', 'doc_id': 'controlDocId', 'labels': 'keywords', 'owner': 'accountableParty', 'sensitivity': 'infoClass', 'space': 'domain', 'title': 'heading', 'updated_at': 'revisedOn', 'url': 'href'}, 'kb-legal-holds': {'acl_principals': 'permittedGroups', 'doc_id': 'matterDocId', 'labels': 'flags', 'owner': 'custodian', 'sensitivity': 'handling', 'space': 'practiceArea', 'title': 'subject', 'updated_at': 'issuedOn', 'url': 'uri'}, 'kb-poa': {'acl_principals': 'readGroups', 'doc_id': 'docRef', 'labels': 'tags', 'owner': 'documentOwner', 'sensitivity': 'handling', 'space': 'practiceGroup', 'title': 'docTitle', 'updated_at': 'lastReviewed', 'url': 'location'}, 'kb-public-web': {'doc_id': 'pageId', 'labels': 'categories', 'owner': 'contentOwner', 'space': 'site', 'title': 'pageTitle', 'updated_at': 'publishedAt', 'url': 'canonicalUrl'}}

KB_DESCRIPTIONS: dict[str, tuple[str, str, bool]] = {'kb-consumer-policy': ('Consumer Banking Policy', 'Deposit, lending and disclosure policy', True), 'kb-ops-runbooks': ('Operations Runbooks', 'Payments and deposit on-call runbooks and decisions', True), 'kb-risk-controls': ('Risk and Controls', 'BSA/AML, SOX and model-risk control narratives', True), 'kb-legal-holds': ('Legal Holds', 'Litigation holds and matter instructions', True), 'kb-poa': ('Power of Attorney', 'Per-state statutory summaries and the internal acceptance procedure', True), 'kb-public-web': ('Public Web', 'Published customer-facing help and disclosure pages', False)}
