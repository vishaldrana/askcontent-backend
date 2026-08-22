"""Finding the terms a corpus uses, so nobody has to type them.

A glossary you must hand-write is a glossary nobody writes. The terms are
already in the documents — as acronyms with their expansion in brackets, as
sentences that define something outright, as capitalised names that recur across
pages — and extracting them turns an empty screen into a review queue.

The same discipline as collections: **this proposes, a human decides.** A
definition invented by the platform and silently trusted is worse than no
glossary, because term resolution is what stops the system substituting a
plausible synonym for a term the corpus does not contain.

Pure: no I/O, no model call. Every proposal carries the sentence it came from,
because a definition without its evidence cannot be judged.
"""

from __future__ import annotations

import re
from collections import defaultdict

from pydantic import BaseModel

#: Acronyms this size are worth proposing. Two letters produce far too many
#: false positives ("US", "AM", "IT" as a pronoun); seven-plus are rarely
#: acronyms at all.
MIN_ACRONYM, MAX_ACRONYM = 3, 6

#: An acronym must come with an expansion, or appear in this many distinct
#: documents, before it is proposed. One mention is a typo as often as a term.
MIN_DOCUMENTS = 2

_STOPWORDS = frozenset("""
THE AND FOR ARE BUT NOT YOU ALL ANY CAN HAD HER WAS ONE OUR OUT DAY GET HAS HIM
HIS HOW MAN NEW NOW OLD SEE TWO WAY WHO BOY DID ITS LET PUT SAY SHE TOO USE
NOTE ONLY THIS THAT WITH FROM WILL EACH WHEN THEY THEM THAN THEN BEEN MUST
""".split())

#: Tokens that look like acronyms and are not terms of art. Deliberately short
#: and deliberately a list: these appear in prose ("POST /v2/payments" is a
#: paragraph, not a code block), so filtering code is not enough. Anything
#: longer than this belongs in the reviewer's hands, not in a keyword file that
#: grows forever.
_TECHNICAL_NOISE = frozenset("POST PUT GET DELETE PATCH HEAD JSON HTML XML CSV UTF ASCII".split())

#: A frequency-only proposal — no expansion, no definition — needs to look like
#: a term of art rather than a repeated word. Three documents is the bar; two
#: proposed too much noise on a technical corpus.
MIN_DOCUMENTS_FREQUENCY = 3

# "Bank Secrecy Act (BSA)" — expansion first.
_EXPANSION_BEFORE = re.compile(
    r"\b((?:[A-Z][A-Za-z'-]+\s+){1,6}[A-Z][A-Za-z'-]+)\s*\(([A-Z]{%d,%d})s?\)"
    % (MIN_ACRONYM, MAX_ACRONYM)
)
# "BSA (Bank Secrecy Act)" — acronym first.
_EXPANSION_AFTER = re.compile(
    r"\b([A-Z]{%d,%d})s?\s*\(((?:[A-Za-z'-]+\s+){1,6}[A-Za-z'-]+)\)"
    % (MIN_ACRONYM, MAX_ACRONYM)
)
_ACRONYM = re.compile(r"\b([A-Z]{%d,%d})s?\b" % (MIN_ACRONYM, MAX_ACRONYM))

#: Sentences that define something outright. The captured group is the term.
#: Anchored at a sentence start. Without the anchor, "A 404 for an existing
#: resource means the caller lacks visibility" yields the term "existing
#: resource" — the article is matched mid-sentence and the real subject is
#: skipped. A definition that names the wrong thing is worse than none.
_DEFINITIONS = [
    re.compile(r"(?:^|(?<=\. ))(?:A|An|The)\s+([a-z][a-z '-]{2,40}?)\s+(?:means|is defined as)\s+([^.]{15,240})\.", re.I),
    re.compile(r"\bFor the purposes of this [a-z]+,\s+[\"']?([A-Za-z][A-Za-z '-]{2,40}?)[\"']?\s+means\s+([^.]{15,240})\.", re.I),
    re.compile(r"\b([A-Z][A-Za-z '-]{2,40}?)\s+refers to\s+([^.]{15,240})\.", ),
]


class TermProposal(BaseModel):
    term: str
    definition: str = ""
    aliases: tuple[str, ...] = ()
    #: expansion | definition | frequency — how it was found, which is how a
    #: reviewer decides how much to trust it.
    method: str = "frequency"
    occurrences: int = 0
    documents: int = 0
    evidence: tuple[str, ...] = ()

    @property
    def confidence(self) -> float:
        base = {"expansion": 0.9, "definition": 0.75, "frequency": 0.4}[self.method]
        spread = min(0.1, 0.02 * self.documents)
        return round(min(1.0, base + spread), 3)


def _sentence_around(text: str, position: int, width: int = 200) -> str:
    start = max(0, text.rfind(". ", 0, position) + 1)
    end = text.find(". ", position)
    end = len(text) if end < 0 else end + 1
    return " ".join(text[start:end][:width].split())


def discover(documents: list[tuple[str, str]], limit: int = 60) -> list[TermProposal]:
    """Propose glossary terms from `(doc_id, text)` pairs."""
    expansions: dict[str, str] = {}
    evidence: dict[str, list[str]] = defaultdict(list)
    doc_count: dict[str, set[str]] = defaultdict(set)
    occurrences: dict[str, int] = defaultdict(int)
    definitions: dict[str, tuple[str, str]] = {}

    for doc_id, text in documents:
        body = text or ""

        for pattern, acronym_group, expansion_group in (
            (_EXPANSION_BEFORE, 2, 1), (_EXPANSION_AFTER, 1, 2),
        ):
            for match in pattern.finditer(body):
                acronym = match.group(acronym_group).upper()
                expansion = " ".join(match.group(expansion_group).split())
                # "The Bank Secrecy Act" is the sentence's article, not the
                # term's.
                expansion = re.sub(r"^(?:The|A|An)\s+", "", expansion)
                if acronym in _STOPWORDS:
                    continue
                expansions.setdefault(acronym, expansion)
                doc_count[acronym].add(doc_id)
                if len(evidence[acronym]) < 3:
                    evidence[acronym].append(_sentence_around(body, match.start()))

        for match in _ACRONYM.finditer(body):
            acronym = match.group(1).upper()
            if acronym in _STOPWORDS:
                continue
            occurrences[acronym] += 1
            doc_count[acronym].add(doc_id)
            if len(evidence[acronym]) < 3:
                evidence[acronym].append(_sentence_around(body, match.start()))

        for pattern in _DEFINITIONS:
            for match in pattern.finditer(body):
                term = " ".join(match.group(1).split()).strip(" '\"")
                if len(term) < 3 or term.lower() in {"document", "policy", "procedure"}:
                    continue
                key = term.lower()
                if key not in definitions:
                    definitions[key] = (term, " ".join(match.group(2).split()))
                    doc_count[key].add(doc_id)
                    if len(evidence[key]) < 3:
                        evidence[key].append(_sentence_around(body, match.start()))

    proposals: list[TermProposal] = []

    for acronym, expansion in expansions.items():
        proposals.append(TermProposal(
            term=acronym, definition=expansion, method="expansion",
            occurrences=occurrences.get(acronym, 1),
            documents=len(doc_count[acronym]),
            evidence=tuple(evidence[acronym]),
            aliases=(expansion,),
        ))

    for key, (term, definition) in definitions.items():
        if term.upper() in expansions:
            continue
        proposals.append(TermProposal(
            term=term, definition=definition, method="definition",
            occurrences=1, documents=len(doc_count[key]),
            evidence=tuple(evidence[key]),
        ))

    seen = {p.term.upper() for p in proposals}
    for acronym, count in occurrences.items():
        if acronym in seen or acronym in _TECHNICAL_NOISE:
            continue
        if len(doc_count[acronym]) < MIN_DOCUMENTS_FREQUENCY:
            continue
        # No expansion found anywhere in the corpus. Proposed because it is
        # clearly a term of art here, and flagged as needing a definition a
        # human supplies — the platform must not invent one.
        proposals.append(TermProposal(
            term=acronym, definition="", method="frequency",
            occurrences=count, documents=len(doc_count[acronym]),
            evidence=tuple(evidence[acronym]),
        ))

    proposals.sort(key=lambda p: (-p.confidence, -p.documents, p.term))
    return proposals[:limit]
