"""Glossary expansion — putting the corpus's words into the reader's question."""

from askcontent.domain.expansion import Term, expand

GLOSSARY = [
    Term("POA", ("power of attorney", "durable POA")),
    Term("terminate", ("cancel", "end a survey")),
    Term("NPS", ("net promoter score",)),
    Term("port", ()),
]


def test_an_acronym_brings_in_its_expansion():
    """The case embeddings cannot do: two capitalised tokens are strings, not
    meanings, and sit no closer in vector space for being synonyms here."""
    out = expand("What does POA mean for a joint account?", GLOSSARY)
    assert "power of attorney" in out.question
    assert out.changed


def test_the_readers_own_words_are_never_replaced():
    """They are the strongest signal available, and a wrong glossary entry
    would otherwise make a question unanswerable in a way nobody can see."""
    out = expand("How do I cancel a survey?", GLOSSARY)
    assert out.question.startswith("How do I cancel a survey?")
    assert "terminate" in out.question


def test_matching_is_whole_word():
    """A substring match turns "important" into a hit for "port", and the
    expanded query is worse than the original."""
    assert not expand("This is important context", GLOSSARY).changed


def test_short_terms_are_case_sensitive():
    """Expanding every "it" into "information technology" ruins every question
    containing a pronoun."""
    glossary = [Term("IT", ("information technology",))]
    assert not expand("How do it work?", glossary).changed
    assert expand("Who runs IT?", glossary).changed


def test_the_longest_surface_form_wins():
    """A corpus with both "plan" and "enterprise plan" should match the
    specific one rather than expanding on the generic."""
    glossary = [Term("enterprise plan", ("business tier",))]
    out = expand("What is in the enterprise plan?", glossary)
    assert "business tier" in out.question


def test_expansion_is_bounded():
    """Past a handful of additions the query stops being about anything and
    vector search returns the centroid of the corpus."""
    many = [Term(f"term{i}", (f"alias {i}", f"other {i}")) for i in range(10)]
    question = " ".join(f"term{i}" for i in range(10))
    assert len(expand(question, many, limit=3).added) == 3


def test_nothing_is_added_when_the_question_already_says_it():
    out = expand("What is a power of attorney (POA)?", GLOSSARY)
    assert "power of attorney" not in out.added


def test_every_addition_is_attributable():
    """An expansion nobody can attribute is one nobody can correct."""
    out = expand("What does POA mean?", GLOSSARY)
    assert out.matched
    assert all(hit and form for hit, form in out.matched)
