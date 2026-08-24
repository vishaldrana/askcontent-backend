# 01 · What happens when somebody uses the widget

`WGT-SEQ-*`

Two diagrams. The first is installation and boot, which happens once per page
load. The second is one question, which is the interesting one.

---

## 1. Boot

The script tag is pasted by somebody who does not work on this product, into a
page nobody on this team can edit. Everything about the boot sequence follows
from that.

```mermaid
sequenceDiagram
    autonumber
    participant P as Host page
    participant Q as askcontent() stub
    participant B as Bundle (embed.js)
    participant S as Session
    participant D as Shadow DOM

    P->>Q: define queueing stub
    P->>Q: askcontent('init', {key, user, baseUrl, …})
    Note over Q: init arrives before the bundle has loaded.<br/>Queued, never lost.
    P->>B: <script src="{API}/widget/embed.js" async>
    B->>Q: read window.askcontent.q
    B->>S: new Session(config)
    S->>S: validate — key required, user.id + user.token required
    Note over S: There is no anonymous mode. An assistant that<br/>does not know who is asking cannot honour<br/>"no answer cites a document the asker cannot open".
    S->>D: mount launcher into a shadow root
    Note over D: Shadow DOM so the host page's CSS cannot<br/>restyle the widget, and ours cannot leak out.
```

**Why the stub matters.** `init` runs before the bundle finishes downloading —
that is the normal case on a real page, not an edge case. A call that arrives
early must be queued rather than lost.

---

## 2. One question

```mermaid
sequenceDiagram
    autonumber
    actor U as Visitor
    participant W as Widget
    participant T as Transport
    participant A as /api/widget/ask
    participant G as Gates
    participant R as Retrieval
    participant M as Answerer

    U->>W: types a question, presses Ask
    W->>W: push user message, push empty assistant message (streaming)
    W->>T: ask(question, onToken, signal)
    T->>A: POST — x-askcontent-key, Authorization: Bearer, Origin

    rect rgb(248, 240, 240)
        Note over A,G: Refuse before doing any work
        A->>G: key → exactly one connector (the client names none)
        G-->>A: 404 if unknown or disabled — same answer for both
        A->>G: Origin on this embed's allowlist?
        G-->>A: 403 with the reason, CORS headers still set
        A->>G: token present?
        G-->>A: 401 — no anonymous mode
        A->>G: connector active?
        G-->>A: 404 if suspended
    end

    A->>A: classify question
    alt about the corpus ("what can you tell me")
        A->>A: build overview from real sections, counts, glossary
        A-->>T: event: token … then event: evidence (no citations)
    else a content question
        A->>R: expand with glossary → search PGP ∥ ECM → fuse
        R->>R: resolve against the store, apply access gates
        R->>R: passages from stored chunks → shortlist → rerank
        R-->>A: citations
        A->>A: relevance gate — does the evidence cover the question?
        alt not covered
            A-->>T: refusal, and zero passages
        else covered
            A->>M: grounded prompt + numbered passages
            loop each token
                M-->>A: text
                A-->>T: event: token
                T-->>W: onToken → patch the streaming message
                W-->>U: text appears as it is written
            end
            A->>A: verify citations — invented? none at all?
            A-->>T: event: evidence (citations, minus the trace)
        end
    end

    T-->>W: resolved Answer
    W->>W: patch message, streaming = false
    W-->>U: answer with its sources
    A->>A: record embed use
```

---

## 3. The decisions inside that diagram

**The client names no connector.** It presents a publishable key, and the key
resolves to exactly one connector server-side. There is no field in which to
put a connector, so a client cannot widen its own reach — the closed-grammar
argument applied to the client.

**The client names no role.** The principal comes from the visitor's token. A
widget that could choose who it was asking as would make every access rule
advisory.

**An unknown key and a disabled embed return the same 404.** Distinguishing
them turns the endpoint into an oracle for which keys exist.

**An empty origin allowlist permits nothing.** That is the safe reading of "no
rules yet", and the opposite of the usual one — which is why it is stated
rather than left to a falsy check.

**The origin is reflected in CORS even when refused.** CORS is not the security
boundary here; the allowlist check is, and it runs on every request. Blocking
at the CORS layer instead would replace an explanatory 403 with the browser's
"Failed to fetch", which is the error somebody spends an afternoon on.

**The trace is stripped.** It names documents a visitor was refused, which is a
question about somebody else's access.

**Prose and evidence are separate events.** `token` frames stream; one
`evidence` frame carries the citations. The prose may vary between runs, the
citations must not, and merging them would make the varying half look as
authoritative as the stable one. The widget **refuses to render prose that
arrived without its evidence frame** — an answer with no evidence is ungrounded
text, which is the one thing this product must never present.

---

## 4. Failure paths

| What happens | What the visitor sees |
|---|---|
| 401 / 403 | "This assistant could not verify who you are. Reload the page and sign in again." |
| 404 | "This assistant is not configured on this page." |
| 429 | "Too many questions at once. Try again in a moment." |
| 5xx | "The assistant is unavailable. Nothing you did caused this." |
| Stream dies mid-answer | The partial answer is discarded — no evidence frame, no render |
| Buffering proxy kills SSE | Falls back to a single JSON response; the answer still arrives |
| Corpus does not cover it | A refusal, and **no** passages |

---

## 5. Installing it

```html
<script>
  (function (w, d) {
    w.askcontent = w.askcontent || function () {
      (w.askcontent.q = w.askcontent.q || []).push(arguments)
    };
    var s = d.createElement('script');
    s.src = 'https://api.example.com/widget/embed.js'; s.async = true;
    d.head.appendChild(s);
  })(window, document);

  askcontent('init', {
    key: 'pk_…',                    // public by construction; authorises nothing
    baseUrl: 'https://api.example.com',
    title: 'Ask the help centre',
    user: { id: CURRENT_USER_ID, token: CURRENT_USER_TOKEN },
  });
</script>
```

React, for applications with their own routing and state:

```jsx
import { AskContent } from '@askcontent/widget/react'

<AskContent publicKey="pk_…" baseUrl="…" user={{ id, token }} />
```

The prop is `publicKey`, not `key` — React reserves `key` and strips it before
a component sees it.

**The token is minted by your server, per visitor, short-lived.** The widget
never sees a credential it could reuse, and the platform never trusts an
identity the page asserted about itself.

> **Not production ready.** The token is currently *not verified* server-side.
> See [production readiness §2.2](../../askcontent-backend/docs/engineering/01-production-readiness.md).
