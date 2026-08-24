# 02 · Design system and theming

`CON-THM-*`

The token system, primitives and shell are **those of the askdb console**. An
operator moving between the two products is in the same application, and that
is a requirement rather than a coincidence.

---

## 1. Owned primitives

**CON-THM-01 (MUST).** These exist as owned source in `src/components/ui/`:
button, card, badge, input, select, table, confidence indicator, state badge,
and a feedback set (loading, empty, error).

**CON-THM-02 (MUST).** Each is editable in place. There is no component library
to upgrade and no theme to fight.

**CON-THM-03 (MUST).** Runtime imports in the component layer are limited to
the framework, an icon set and the router. One class-name helper is permitted.

---

## 2. Tokens

**CON-THM-04 (MUST).** The palette is expressed as CSS variables holding **bare
channel triples**, not complete colour values.

**Why.** The utility framework's opacity modifiers resolve a colour function
around the variable. Storing a complete colour breaks **every** opacity
modifier in the codebase, and they are used widely for subtle surfaces.

**CON-THM-05 (MUST).** The sidebar is **its own surface family**, not the card
colour reused.

**CON-THM-06 (MUST).** In dark mode, cards are **lighter** than the page, so
surfaces read as raised. Reversing it flattens the whole application.

**CON-THM-07 (MUST).** Both themes are complete. A colour is never defined only
inside a dark-mode block.

---

## 3. Typography and density

**CON-THM-08 (MUST).** Two families: an interface family and a monospace family
for identifiers, hashes, paths and document ids. Both bundled locally — a font
fetched from a public host does not load inside the target network.

**CON-THM-09 (MUST).** Identifiers, hashes and paths are always monospace
(`.ident`).

**CON-THM-10 (MUST).** Numeric table columns are right-aligned and use tabular
figures (`.num`).

---

## 4. Semantic presentation

**CON-THM-11 (MUST).** A document type is shown as a **badge with its
evidence** in the title attribute, never as a raw enumeration value.

**CON-THM-12 (MUST).** Confidence is a compact visual indicator **plus** its
numeric value, and it is sortable.

**Why.** Sorting by confidence ascending is the fastest path to a correctly
reviewed corpus. The indicator is not decoration.

**CON-THM-13 (MUST).** Authority tiers on citations are visually distinct. An
authoritative policy and a stale supporting page must not look the same.

**CON-THM-14 (MUST).** Staleness is always shown next to a date, never instead
of one.

---

## 5. Accessibility

**CON-THM-15 (MUST).** Visible focus rings everywhere. These screens are
operated by keyboard-heavy administrators.

**CON-THM-16 (MUST).** Colour is never the only carrier of meaning: pair it
with a label, an icon or a glyph.

**CON-THM-17 (MUST).** Everything animated is disabled under a reduced-motion
preference.
