# 00 · The embeddable widget

`WGT-*`

An embeddable assistant that answers questions about a bounded set of company
content. One snippet on a page; a reader asks in plain English; the answer comes
back with **the same guardrails the console has** — every claim carries a
citation, refusals are refusals, and nothing is cited that the reader cannot
open.

---

## 1. Installation shape

**WGT-01 (MUST).** Installation is **one script tag** carrying a queueing stub
and a single initialisation call taking a publishable key and the reader's
identity. Everything else has a default set on the embed, and the page may
override any of it.

**Why the stub.** The snippet is pasted into pages by people who are not the
people who wrote the application. A call that arrives before the bundle
finishes loading must be queued, not lost.

**WGT-02 (MUST).** Identity is **required**. There is no anonymous mode.

**Why, and this one is not negotiable.** The product's central rule is that no
answer may cite a document the asker cannot open. An assistant that does not
know who is asking cannot honour it, and would answer every question from the
connector's full corpus — which is precisely the leak the whole containment
model exists to prevent.

**WGT-03 (MUST).** A separate **React package** is published for use inside a
React application.

**Why both.** A script tag is right for a marketing site, or anywhere the person
pasting it is not the person who wrote the application. Inside a React
application it is the wrong shape: a global initialiser has to be driven from an
effect, it outlives the route that wanted it, and it cannot take a value from
application state without an imperative call to keep the two in step.

**WGT-04 (MUST).** Both packages are built from **one engine**. Session,
transport, stream handling, message cap and the citation model live in one
shared module. **Only rendering differs**, because that is the only part that
should.

---

## 2. Size and dependencies

**WGT-05 (MUST).** **Zero runtime dependencies.** React is a peer dependency of
the React package and is never bundled — a second copy of React on the host's
page is a subtle and expensive bug.

**WGT-06 (MUST).** Markdown is rendered by a small bundled renderer that
**escapes first and adds tags second**, so nothing in an answer can become
markup.

**Why this matters more here than in most widgets.** Answers are assembled from
content that anyone in the company can edit. Treating an answer as trusted HTML
would turn every wiki page into a cross-site scripting vector on the host's
site.

**WGT-07 (MUST).** Links are restricted to `http` and `https`. Every other
scheme is **dropped**, not sanitised — a sanitiser is a thing to be outwitted
and an allowlist is not.

**WGT-08 (MUST).** Budget: **≤ 20 KB compressed**, one request. Target ~14 KB.
The size check runs in the build and **fails it**.

**Why a failure and not a report.** A budget that is only reported is a budget
that gets exceeded on a Friday and noticed the following quarter. Removing a
feature is the correct response to breaching it; raising the number is a
decision, not a fix.

*Current: 14.5 KB raw, 5.4 KB gzipped.*

---

## 3. Configuration surface

**WGT-09 (MUST).** The initialisation call accepts:

| Group | Contents |
|---|---|
| Identity | `key` (publishable), `user.id`, `user.token`, optional `user.name` |
| Placement | `position`, `size` (`compact` / `standard` / `full`), `open` |
| Presentation | `title`, `placeholder`, `theme` (`light` / `dark` / `auto`) |
| Limits | `maxMessages` |
| Telemetry | `onEvent` |

**WGT-10 (MUST).** The publishable key resolves to **one connector**
server-side. The widget cannot name a connector, and there is no field in which
to put one.

**Why.** The same reasoning as the retrieval grammar: a client that can name its
own corpus can widen its own reach. Making it unrepresentable is cheaper than
validating it.

**WGT-11 (MUST).** `onEvent` receives `opened`, `closed`, `asked`, `answered`,
`citation_clicked` and `error`. A host callback that throws must not break the
widget.

---

## 4. Imperative methods

**WGT-12 (MUST).** `open`, `close`, `ask`, `clear`, `destroy`.

**WGT-13 (MUST).** `ask` **opens the panel**. A question asked from the host
page that answers into a closed widget is a question nobody sees.

**WGT-14 (MUST).** `destroy` aborts any request in flight and removes the host
element. A single-page application that navigates away must not leave a
streaming request running.

**WGT-15 (MUST).** One instance per page. A second `init` is ignored rather
than stacking a second launcher.

---

## 5. What answers look like

**WGT-16 (MUST).** The response carries **prose and evidence separately**. The
widget renders the evidence as the record and the prose as commentary on it.

**Why.** The prose may vary between runs; the citations must not. Presenting
them as one blob makes the varying part look as authoritative as the stable
one.

**WGT-17 (MUST).** An answer whose evidence frame never arrived is **discarded
with an error**, not rendered. Showing the prose alone would present ungrounded
text as an answer, which is the single thing this product must never do.

**WGT-18 (MUST).** Every citation renders: title, authority tier, staleness,
last-modified date, owner where known, heading path, and the supporting span.

**WGT-19 (MUST).** The citation link opens the document **in the ECM**, never
in our copy. Our parsed copy can be stale, and sending the reader to it makes us
the system of record for content we do not own.

**WGT-20 (MUST).** Conflicts render **above** the citation list, with both
sources' dates and tiers. The widget never picks between them and never hides
one because it ranked lower.

**WGT-21 (MUST).** A refusal renders as an **answer**, not as an error state.
Where the refusal is a permission boundary, the content is never summarised,
paraphrased or hinted at.

**WGT-22 (MUST).** `degraded` is rendered whenever present. A channel that
failed is named; silent narrowing of the evidence base is prohibited here
exactly as it is in the console.

---

## 6. The awkward parts

**WGT-23 (MUST).** Styles live inside a **shadow root**. The host page cannot
restyle the widget and the widget cannot leak into the host page. Theming is
done through custom properties, which needs no build.

**WGT-24 (MUST).** A deployment behind a buffering proxy loses the stream but
**must not lose the answer**: a plain JSON response is a first-class case in the
transport, not a fallback bolted on later.

**WGT-25 (MUST).** Error messages are written for a reader, not an operator:
*"This assistant could not verify who you are. Reload the page and sign in
again."* — never a status code.

**WGT-26 (MUST).** The message list is capped and trimmed from the front. A
widget left open on a dashboard for a week must not become a memory leak.

**WGT-27 (MUST).** Asking while a request is in flight is ignored rather than
queued, and cancelling clears the placeholder rather than leaving a half
message.

**WGT-28 (MUST).** Animation is disabled under a reduced-motion preference, the
panel is a labelled dialog, the log is a polite live region, and focus returns
to the input when a request settles.

---

## 7. Development and safety

**WGT-29 (MUST).** The bundle URL is **unversioned**, because the snippet is
pasted into pages nobody will edit again. It is cached for approximately an
hour, not a year, so a fix reaches those pages.

**WGT-30 (MUST).** If the bundle is absent from the expected location, the
endpoint says so **in plain words** rather than returning a not-found.

**Why.** "We forgot to build the widget" and "your URL is wrong" are different
problems, and the person hitting the endpoint cannot tell them apart from a
generic 404.

**WGT-31 (MUST).** The publishable key is public by construction. Every
authorisation decision is made server-side against `user.token`; the key
identifies the embed, it does not authorise anything.
