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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-15T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2025-06-18T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-05-18T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2023-04-30T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-08-05T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-08-13T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2025-12-25T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2022-10-22T12:00:00+00:00'),
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
        missing_from_store=True,
        updated_at=dt.datetime.fromisoformat('2026-06-11T12:00:00+00:00'),
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
        missing_from_store=False,
        forbidden_for=('user:asha', 'group:all-staff', 'group:consumer-banking'),
        updated_at=dt.datetime.fromisoformat('2026-05-26T12:00:00+00:00'),
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
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-27T12:00:00+00:00'),
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
        missing_from_store=False,
        forbidden_for=('group:all-staff', 'user:asha'),
        updated_at=dt.datetime.fromisoformat('2026-04-04T12:00:00+00:00'),
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
        missing_from_store=False,
        forbidden_for=('group:all-staff', 'user:asha', 'user:rob'),
        updated_at=dt.datetime.fromisoformat('2026-08-10T12:00:00+00:00'),
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
        missing_from_store=False,
        canonical_of='RSK-POL-3301',
        updated_at=dt.datetime.fromisoformat('2026-01-19T12:00:00+00:00'),
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
        missing_from_store=False,
        index_title_override='Fraud Protection (draft)',
        updated_at=dt.datetime.fromisoformat('2026-06-19T12:00:00+00:00'),
        body_html='<html><body>\n<h2>Zero liability</h2>\n<p>Customers are not held responsible for unauthorised card transactions reported promptly.</p>\n<h2>Reporting a suspected fraud</h2>\n<p>Report suspected fraud immediately through the mobile app, online banking, or by calling the number on the back of the card.</p>\n<h2>Frequently asked</h2>\n<p>How quickly must I report an unauthorised transaction?</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='LGL-POA-INDEX',
        kb_id='kb-poa',
        title='Power of Attorney',
        path='/legal/poa',
        space='POA',
        owner='general.counsel@wellsfargo.example',
        labels=('poa', 'section-page'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-23T12:00:00+00:00'),
        body_html='<html><body><h2>Power of Attorney</h2><p>Everything the bank holds on powers of attorney: the per-state statutory summaries maintained by counsel, and the internal acceptance procedure maintained by operations.</p><h2>Pages in this section</h2><ul><li><a href="https://ecm.example.com/legal/poa/state-guidelines">State guidelines</a></li><li><a href="https://ecm.example.com/legal/poa/internal">Internal procedure</a></li></ul></body></html>',
    ),
    SeedDoc(
        doc_id='LGL-POA-STATES',
        kb_id='kb-poa',
        title='State guidelines',
        path='/legal/poa/state-guidelines',
        space='POA',
        owner='general.counsel@wellsfargo.example',
        labels=('poa', 'section-page'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-23T12:00:00+00:00'),
        body_html='<html><body><h2>State guidelines</h2><p>One summary per jurisdiction. Where an instrument names a governing law other than the state of the account, the named law controls.</p><h2>Pages in this section</h2><ul><li><a href="https://ecm.example.com/legal/poa/state-guidelines/california">Power of Attorney: California Statutory Summary</a></li><li><a href="https://ecm.example.com/legal/poa/state-guidelines/new york">Power of Attorney: New York Statutory Summary</a></li><li><a href="https://ecm.example.com/legal/poa/state-guidelines/texas">Power of Attorney: Texas Statutory Summary</a></li><li><a href="https://ecm.example.com/legal/poa/state-guidelines/florida">Power of Attorney: Florida Statutory Summary</a></li><li><a href="https://ecm.example.com/legal/poa/state-guidelines/illinois">Power of Attorney: Illinois Statutory Summary</a></li></ul></body></html>',
    ),
    SeedDoc(
        doc_id='LGL-POA-INTERNAL',
        kb_id='kb-poa',
        title='Internal procedure',
        path='/legal/poa/internal',
        space='POA',
        owner='general.counsel@wellsfargo.example',
        labels=('poa', 'section-page'),
        sensitivity='internal',
        acl_principals=('group:all-staff',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-23T12:00:00+00:00'),
        body_html='<html><body><h2>Internal procedure</h2><p>How the bank reviews, accepts, records and refuses a presented power of attorney.</p><h2>Pages in this section</h2><ul><li><a href="https://ecm.example.com/legal/poa/internal/acceptance-procedure">Acceptance procedure</a></li><li><a href="https://ecm.example.com/legal/poa/internal/agent-verification-standard">Agent verification standard</a></li></ul></body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-HOME',
        kb_id='kb-techdocs',
        title='Engineering Documentation',
        path='/wiki/spaces/ENG/overview',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'overview', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-08-10T12:00:00+00:00'),
        body_html="<html><head><title>{}</title></head><body>\n<h2>What lives here</h2>\n<p>Reference documentation for the platform: the public API, the services behind it, the runbooks that keep them up and the decisions that shaped them. Written for engineers joining a team, and for the on-call engineer at 3am who has never seen this service before.</p>\n<h2>Sections</h2>\n<ul><li><a href='/wiki/spaces/ENG/api-reference'>API reference</a></li><li><a href='/wiki/spaces/ENG/services'>Service catalog</a></li><li><a href='/wiki/spaces/ENG/decisions'>Architecture decisions</a></li><li><a href='/wiki/spaces/ENG/onboarding'>Onboarding</a></li></ul>\n<h2>Conventions</h2>\n<p>Every service page carries the same headings so a reader can jump to the section they need without reading the page. Where a page and the code disagree, the code is right and the page is a defect.</p>\n</body></html>",
    ),
    SeedDoc(
        doc_id='ENG-DOC-API',
        kb_id='kb-techdocs',
        title='API reference',
        path='/wiki/spaces/ENG/api-reference',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'api', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-08-16T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Base URLs</h2>\n<table><tr><th>Environment</th><th>Base URL</th><th>Data</th></tr><tr><td>Sandbox</td><td>https://api.sandbox.example/v2</td><td>Synthetic only</td></tr><tr><td>Staging</td><td>https://api.staging.example/v2</td><td>Masked production copy</td></tr><tr><td>Production</td><td>https://api.example/v2</td><td>Live</td></tr></table>\n<h2>Authentication</h2>\n<p>Every request carries a bearer token obtained through the client-credentials flow. Tokens are valid for <b>15 minutes</b> and are not refreshable — request a new one.</p><pre><code class="language-bash">curl -X POST https://auth.example/oauth2/token \\\n  -d grant_type=client_credentials \\\n  -d scope=\'payments:write accounts:read\' \\\n  -u "$CLIENT_ID:$CLIENT_SECRET"</code></pre>\n<h2>Idempotency</h2>\n<p>Every mutating request must carry an <code>Idempotency-Key</code> header. Keys are retained for <b>24 hours</b>. Resending an identical body with the same key returns the original response and does not create a second payment.</p><pre><code class="language-bash">curl -X POST https://api.example/v2/payments \\\n  -H "Authorization: Bearer $TOKEN" \\\n  -H "Idempotency-Key: $(uuidgen)" \\\n  -H \'Content-Type: application/json\' \\\n  -d @payment.json</code></pre>\n<h2>Rate limits</h2>\n<p>Quotas are per client and per environment, measured over a sliding 60-second window.</p><table><tr><th>Tier</th><th>Requests / minute</th><th>Burst</th></tr><tr><td>Sandbox</td><td>60</td><td>10</td></tr><tr><td>Standard</td><td>600</td><td>60</td></tr><tr><td>High volume</td><td>6000</td><td>600</td></tr></table><p>Exceeding the quota returns <code>429</code> with a <code>Retry-After</code> header in seconds. Retrying before that interval counts against the next window and extends the block.</p>\n<h2>Pagination</h2>\n<p>List endpoints are cursor-paginated. Pass the <code>next_cursor</code> from the previous response; do not construct cursors.</p><pre><code class="language-json">{\n  "data": [ ... ],\n  "next_cursor": "eyJvIjoxMDB9",\n  "has_more": true\n}</code></pre>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-ERRORS',
        kb_id='kb-techdocs',
        title='Error codes',
        path='/wiki/spaces/ENG/api-reference/errors',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'api', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-08-16T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Envelope</h2>\n<p>Every error shares one envelope. The <code>code</code> is stable and safe to branch on; the <code>message</code> is for humans and may change.</p><pre><code class="language-json">{\n  "code": "insufficient_scope",\n  "message": "Token lacks payments:write",\n  "required_scope": "payments:write",\n  "request_id": "req_9f2a11"\n}</code></pre>\n<h2>Codes</h2>\n<table><tr><th>HTTP</th><th>Code</th><th>Meaning</th><th>What to do</th></tr><tr><td>400</td><td>invalid_request</td><td>The request body failed schema validation.</td><td>Fix the request. The `errors` array names each failing field.</td></tr><tr><td>401</td><td>unauthenticated</td><td>No credential, or the credential has expired.</td><td>Obtain a new token. Tokens live for 15 minutes.</td></tr><tr><td>403</td><td>insufficient_scope</td><td>The token is valid but lacks the required scope.</td><td>Request the scope named in `required_scope` at authorisation time.</td></tr><tr><td>404</td><td>not_found</td><td>The resource does not exist, or is not visible to this caller.</td><td>Do not retry. A 404 for an existing resource means the caller lacks visibility.</td></tr><tr><td>409</td><td>idempotency_conflict</td><td>The idempotency key was reused with a different body.</td><td>Use a fresh key, or resend the identical body to get the original response.</td></tr><tr><td>422</td><td>unprocessable</td><td>Well-formed but not actionable — a closed account, a past value date.</td><td>Correct the instruction. Retrying without change will fail identically.</td></tr><tr><td>429</td><td>rate_limited</td><td>The caller exceeded its quota for the window.</td><td>Retry after the interval in the `Retry-After` header. Do not retry sooner.</td></tr><tr><td>503</td><td>upstream_unavailable</td><td>A downstream rail is not accepting instructions.</td><td>Retry with exponential backoff and full jitter, up to the value-date deadline.</td></tr></table>\n<h2>Retries</h2>\n<p>Retry <code>429</code> and <code>503</code> only. Everything else is deterministic and will fail identically. Use exponential backoff with full jitter, and stop at the value-date deadline rather than at a retry count.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-SERVICES',
        kb_id='kb-techdocs',
        title='Service catalog',
        path='/wiki/spaces/ENG/services',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'catalog', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-08-13T12:00:00+00:00'),
        body_html="<html><head><title>{}</title></head><body>\n<h2>Services</h2>\n<table><tr><th>Service</th><th>Tier</th><th>SLO</th><th>Owner</th></tr><tr><td><a href='/wiki/spaces/ENG/services/payments-api'>Payments API</a></td><td>1</td><td>99.95%</td><td>payments-platform@wellsfargo.example</td></tr><tr><td><a href='/wiki/spaces/ENG/services/ledger'>Ledger</a></td><td>0</td><td>99.99%</td><td>core-ledger@wellsfargo.example</td></tr><tr><td><a href='/wiki/spaces/ENG/services/identity'>Identity</a></td><td>0</td><td>99.99%</td><td>identity-platform@wellsfargo.example</td></tr><tr><td><a href='/wiki/spaces/ENG/services/documents'>Document Service</a></td><td>2</td><td>99.9%</td><td>content-platform@wellsfargo.example</td></tr><tr><td><a href='/wiki/spaces/ENG/services/notifications'>Notifications</a></td><td>2</td><td>99.5%</td><td>engagement@wellsfargo.example</td></tr><tr><td><a href='/wiki/spaces/ENG/services/fraud-signals'>Fraud Signals</a></td><td>0</td><td>99.99%</td><td>fraud-platform@wellsfargo.example</td></tr></table>\n<h2>Tier definitions</h2>\n<p>Tier 0 is customer-money-moving and pages at any error budget burn. Tier 1 is customer-facing and pages in hours. Tier 2 is internal and raises a ticket.</p>\n</body></html>",
    ),
    SeedDoc(
        doc_id='ENG-DOC-SVC-PAYMENTSAPI',
        kb_id='kb-techdocs',
        title='Payments API',
        path='/wiki/spaces/ENG/services/payments-api',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'service', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-14T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Purpose</h2>\n<p>Initiates and tracks outbound payments: wires, ACH and internal book transfers.</p>\n<h2>Ownership</h2>\n<table><tr><th>Owner</th><td>payments-platform@wellsfargo.example</td></tr><tr><th>Tier</th><td>1</td></tr><tr><th>Availability SLO</th><td>99.95%</td></tr><tr><th>Latency objective</th><td>p99 240 ms</td></tr></table>\n<h2>Primary endpoint</h2>\n<p><code>POST /v2/payments</code></p><pre><code class="language-json">{\n  "amount": {"value": "1500.00", "currency": "USD"},\n  "rail": "fedwire",\n  "debtor_account": "acct_8812",\n  "creditor": {"routing": "121000248", "account": "0198822"},\n  "value_date": "2026-08-24"\n}</code></pre>\n<h2>Deployment</h2>\n<p>Deployed continuously from the main branch behind a progressive rollout: 1% for ten minutes, 25% for thirty, then full. A rollback is a redeploy of the previous image and takes about four minutes.</p><pre><code class="language-bash">deployctl rollout status --service payments-api\ndeployctl rollback --service payments-api --to previous</code></pre>\n<h2>Observability</h2>\n<p>Dashboards are linked from the service page in the operations portal. The golden signals are request rate, error ratio, p99 latency and saturation of the primary dependency.</p>\n<h2>On-call</h2>\n<p>Escalates to the owning team. The most common incident is covered by the runbook <i>Fedwire cut-off breach</i>.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-SVC-LEDGER',
        kb_id='kb-techdocs',
        title='Ledger',
        path='/wiki/spaces/ENG/services/ledger',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'service', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-13T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Purpose</h2>\n<p>Double-entry book of record for all internal movement. Append-only.</p>\n<h2>Ownership</h2>\n<table><tr><th>Owner</th><td>core-ledger@wellsfargo.example</td></tr><tr><th>Tier</th><td>0</td></tr><tr><th>Availability SLO</th><td>99.99%</td></tr><tr><th>Latency objective</th><td>p99 45 ms</td></tr></table>\n<h2>Primary endpoint</h2>\n<p><code>POST /v1/entries</code></p><pre><code class="language-json">{\n  "journal": "jrnl_2026_08",\n  "lines": [\n    {"account": "1001", "direction": "debit", "amount": "1500.00"},\n    {"account": "2100", "direction": "credit", "amount": "1500.00"}\n  ]\n}</code></pre>\n<h2>Deployment</h2>\n<p>Deployed continuously from the main branch behind a progressive rollout: 1% for ten minutes, 25% for thirty, then full. A rollback is a redeploy of the previous image and takes about four minutes.</p><pre><code class="language-bash">deployctl rollout status --service ledger\ndeployctl rollback --service ledger --to previous</code></pre>\n<h2>Observability</h2>\n<p>Dashboards are linked from the service page in the operations portal. The golden signals are request rate, error ratio, p99 latency and saturation of the primary dependency.</p>\n<h2>On-call</h2>\n<p>Escalates to the owning team. The most common incident is covered by the runbook <i>Core deposit posting lag</i>.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-SVC-IDENTITY',
        kb_id='kb-techdocs',
        title='Identity',
        path='/wiki/spaces/ENG/services/identity',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'service', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-12T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Purpose</h2>\n<p>Customer authentication, session issuance and step-up challenges.</p>\n<h2>Ownership</h2>\n<table><tr><th>Owner</th><td>identity-platform@wellsfargo.example</td></tr><tr><th>Tier</th><td>0</td></tr><tr><th>Availability SLO</th><td>99.99%</td></tr><tr><th>Latency objective</th><td>p99 90 ms</td></tr></table>\n<h2>Primary endpoint</h2>\n<p><code>POST /v1/sessions</code></p><pre><code class="language-json">{\n  "customer_id": "cus_44120",\n  "factors": ["password", "device"],\n  "scope": ["accounts:read", "payments:write"]\n}</code></pre>\n<h2>Deployment</h2>\n<p>Deployed continuously from the main branch behind a progressive rollout: 1% for ten minutes, 25% for thirty, then full. A rollback is a redeploy of the previous image and takes about four minutes.</p><pre><code class="language-bash">deployctl rollout status --service identity\ndeployctl rollback --service identity --to previous</code></pre>\n<h2>Observability</h2>\n<p>Dashboards are linked from the service page in the operations portal. The golden signals are request rate, error ratio, p99 latency and saturation of the primary dependency.</p>\n<h2>On-call</h2>\n<p>Escalates to the owning team. The most common incident is covered by the runbook <i>Session store failover</i>.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-SVC-DOCUMENTS',
        kb_id='kb-techdocs',
        title='Document Service',
        path='/wiki/spaces/ENG/services/documents',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'service', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-11T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Purpose</h2>\n<p>Stores and serves customer documents: statements, disclosures, signed instruments.</p>\n<h2>Ownership</h2>\n<table><tr><th>Owner</th><td>content-platform@wellsfargo.example</td></tr><tr><th>Tier</th><td>2</td></tr><tr><th>Availability SLO</th><td>99.9%</td></tr><tr><th>Latency objective</th><td>p99 600 ms</td></tr></table>\n<h2>Primary endpoint</h2>\n<p><code>GET /v1/documents/{id}</code></p><pre><code class="language-json">{\n  "id": "doc_9912",\n  "kind": "statement",\n  "period": "2026-07",\n  "content_type": "application/pdf"\n}</code></pre>\n<h2>Deployment</h2>\n<p>Deployed continuously from the main branch behind a progressive rollout: 1% for ten minutes, 25% for thirty, then full. A rollback is a redeploy of the previous image and takes about four minutes.</p><pre><code class="language-bash">deployctl rollout status --service documents\ndeployctl rollback --service documents --to previous</code></pre>\n<h2>Observability</h2>\n<p>Dashboards are linked from the service page in the operations portal. The golden signals are request rate, error ratio, p99 latency and saturation of the primary dependency.</p>\n<h2>On-call</h2>\n<p>Escalates to the owning team. The most common incident is covered by the runbook <i>Document render backlog</i>.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-SVC-NOTIFICATIONS',
        kb_id='kb-techdocs',
        title='Notifications',
        path='/wiki/spaces/ENG/services/notifications',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'service', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-10T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Purpose</h2>\n<p>Delivers push, SMS and email, with per-channel preference and quiet hours.</p>\n<h2>Ownership</h2>\n<table><tr><th>Owner</th><td>engagement@wellsfargo.example</td></tr><tr><th>Tier</th><td>2</td></tr><tr><th>Availability SLO</th><td>99.5%</td></tr><tr><th>Latency objective</th><td>p99 1.2 s</td></tr></table>\n<h2>Primary endpoint</h2>\n<p><code>POST /v1/notifications</code></p><pre><code class="language-json">{\n  "customer_id": "cus_44120",\n  "template": "wire_sent",\n  "channels": ["push", "email"]\n}</code></pre>\n<h2>Deployment</h2>\n<p>Deployed continuously from the main branch behind a progressive rollout: 1% for ten minutes, 25% for thirty, then full. A rollback is a redeploy of the previous image and takes about four minutes.</p><pre><code class="language-bash">deployctl rollout status --service notifications\ndeployctl rollback --service notifications --to previous</code></pre>\n<h2>Observability</h2>\n<p>Dashboards are linked from the service page in the operations portal. The golden signals are request rate, error ratio, p99 latency and saturation of the primary dependency.</p>\n<h2>On-call</h2>\n<p>Escalates to the owning team. The most common incident is covered by the runbook <i>Delivery provider failover</i>.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-ADR-014',
        kb_id='kb-techdocs',
        title='ADR 014: idempotency keys are caller-supplied',
        path='/wiki/spaces/ENG/decisions/adr-014-idempotency',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'adr', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-02-13T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Status</h2>\n<p>Status: Accepted. Superseded by nothing.</p>\n<h2>Context</h2>\n<p>Context: retries are unavoidable across a payment rail, and a server-generated key cannot be known by a caller whose request timed out before the response arrived — which is precisely when a retry happens.</p>\n<h2>Decision</h2>\n<p>Decision: the caller supplies the key. The server stores it against the request hash for 24 hours and replays the original response on an identical resend.</p>\n<h2>Consequences</h2>\n<p>Consequences: a caller that generates a fresh key on retry will double-pay. The SDKs generate the key once per logical operation, and the API reference says so in bold.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-ADR-021',
        kb_id='kb-techdocs',
        title='ADR 021: cursor pagination, never offset',
        path='/wiki/spaces/ENG/decisions/adr-021-pagination',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'adr', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2025-12-25T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Status</h2>\n<p>Status: Accepted</p>\n<h2>Context</h2>\n<p>Context: offset pagination over an append-heavy table skips and duplicates rows as the table grows under the reader.</p>\n<h2>Decision</h2>\n<p>Decision: opaque cursors encoding the sort key. Callers must not construct or parse them.</p>\n<h2>Consequences</h2>\n<p>Consequences: no random access to page N. Two integrations asked for it and were told to filter instead.</p>\n</body></html>',
    ),
    SeedDoc(
        doc_id='ENG-DOC-ONBOARD',
        kb_id='kb-techdocs',
        title='Onboarding',
        path='/wiki/spaces/ENG/onboarding',
        space='ENG',
        owner='platform-docs@wellsfargo.example',
        labels=('techdocs', 'onboarding', 'approved'),
        sensitivity='internal',
        acl_principals=('group:engineering',),
        version='1',
        missing_from_store=False,
        updated_at=dt.datetime.fromisoformat('2026-07-20T12:00:00+00:00'),
        body_html='<html><head><title>{}</title></head><body>\n<h2>Day one</h2>\n<p>Request access to the sandbox tenant, install the toolchain and run the smoke suite. If the suite passes locally your environment is correct; if it does not, stop and ask rather than working around it.</p><pre><code class="language-bash">brew bundle --file=Brewfile\nmake bootstrap\nmake test-smoke</code></pre>\n<h2>Getting a token</h2>\n<p>Sandbox credentials are issued per engineer and expire every 90 days.</p><pre><code class="language-bash">export CLIENT_ID=$(op read \'op://eng/sandbox/client_id\')\nexport CLIENT_SECRET=$(op read \'op://eng/sandbox/client_secret\')\nmake token</code></pre>\n<h2>Your first payment</h2>\n<p>The sandbox settles instantly and reverses nightly, so a mistake there costs nothing.</p><pre><code class="language-bash">make payment AMOUNT=10.00 RAIL=ach</code></pre>\n<h2>Where to ask</h2>\n<p>The team channel first, the platform channel if it is not your team\'s service, and the on-call rotation only for something that is currently broken.</p>\n</body></html>',
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
        missing_from_store=True,
        updated_at=dt.datetime.fromisoformat('2025-04-09T12:00:00+00:00'),
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

KB_FIELD_VOCABULARY: dict[str, dict[str, str]] = {'kb-consumer-policy': {'acl_principals': 'readGroups', 'doc_id': 'documentNumber', 'labels': 'tags', 'owner': 'ownerEmail', 'sensitivity': 'classification', 'space': 'spaceKey', 'title': 'docTitle', 'updated_at': 'lastModified', 'url': 'portalUrl'}, 'kb-ops-runbooks': {'acl_principals': 'acl', 'doc_id': 'id', 'labels': 'topics', 'owner': 'maintainer', 'sensitivity': 'level', 'space': 'team', 'title': 'name', 'updated_at': 'updated', 'url': 'link'}, 'kb-risk-controls': {'acl_principals': 'entitlements', 'doc_id': 'controlDocId', 'labels': 'keywords', 'owner': 'accountableParty', 'sensitivity': 'infoClass', 'space': 'domain', 'title': 'heading', 'updated_at': 'revisedOn', 'url': 'href'}, 'kb-legal-holds': {'acl_principals': 'permittedGroups', 'doc_id': 'matterDocId', 'labels': 'flags', 'owner': 'custodian', 'sensitivity': 'handling', 'space': 'practiceArea', 'title': 'subject', 'updated_at': 'issuedOn', 'url': 'uri'}, 'kb-poa': {'acl_principals': 'readGroups', 'doc_id': 'docRef', 'labels': 'tags', 'owner': 'documentOwner', 'sensitivity': 'handling', 'space': 'practiceGroup', 'title': 'docTitle', 'updated_at': 'lastReviewed', 'url': 'location'}, 'kb-techdocs': {'acl_principals': 'viewRestrictions', 'doc_id': 'contentId', 'labels': 'labels', 'owner': 'lastUpdatedBy', 'sensitivity': 'restrictionLevel', 'space': 'spaceKey', 'title': 'pageTitle', 'updated_at': 'lastUpdated', 'url': 'webui'}, 'kb-public-web': {'doc_id': 'pageId', 'labels': 'categories', 'owner': 'contentOwner', 'space': 'site', 'title': 'pageTitle', 'updated_at': 'publishedAt', 'url': 'canonicalUrl'}}

KB_DESCRIPTIONS: dict[str, tuple[str, str, bool]] = {'kb-consumer-policy': ('Consumer Banking Policy', 'Deposit, lending and disclosure policy', True), 'kb-ops-runbooks': ('Operations Runbooks', 'Payments and deposit on-call runbooks and decisions', True), 'kb-risk-controls': ('Risk and Controls', 'BSA/AML, SOX and model-risk control narratives', True), 'kb-legal-holds': ('Legal Holds', 'Litigation holds and matter instructions', True), 'kb-poa': ('Power of Attorney', 'Per-state statutory summaries and the internal acceptance procedure', True), 'kb-techdocs': ('Engineering Documentation', 'Confluence space: API reference, service catalog, runbooks, decisions', True), 'kb-public-web': ('Public Web', 'Published customer-facing help and disclosure pages', False)}
