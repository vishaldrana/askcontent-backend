# The askcontent documentation

Every repository's documentation lives here, not beside its code.

Four repositories build one product — a backend, a console, an embeddable
widget and a sample corpus — and the decisions worth writing down almost never
belong to exactly one of them. Where the citation markers are rendered is a
console and a widget decision; what a `[page]` marker *means* is a backend one;
the three of them are one paragraph. Split across four `docs/` folders, that
paragraph got written once, in whichever repository the author had open, and
the other three said nothing.

So the record is single. Each repository keeps a `docs/README.md` pointing
here, because somebody who opens the console looking for its screens should
not have to guess.

| Where | What |
| --- | --- |
| `design/` | What the system is and why. Numbered in the order the pieces were designed, not the order they were built. |
| `engineering/` | How it is run: the decision log, production readiness, reranking, deep research. |
| `console/` | The admin application — its HTTP contract, its screens, its design system. |
| `widget/` | The embeddable assistant — its contract and its sequence. |
| `sample-data/` | The corpora used for demonstration and evaluation. |
