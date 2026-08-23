"""What an empty chat offers, and what it refuses to invent."""

from dataclasses import dataclass

from askcontent.domain.starters import MAX_LABEL_CHARS, choose


@dataclass
class Doc:
    title: str
    path: str | None = None
    space: str | None = None
    size_bytes: int | None = 1000
    doc_id: str = "d"


def test_spreads_across_sections_before_filling_by_size():
    docs = [
        Doc("Advance Survey Design", "/product-guide/survey-designer/advance", size_bytes=9000),
        Doc("Branching Logic", "/product-guide/survey-designer/branching", size_bytes=8000),
        Doc("Skip Logic", "/product-guide/survey-designer/skip", size_bytes=7000),
        Doc("Android Survey SDK", "/product-guide/installation/android", size_bytes=1000),
    ]
    picked = [s.label for s in choose(docs, limit=2)]
    # The installation page is a tenth the size of the ones it beat. It is
    # there because it is the only thing representing its section.
    assert picked == ["Advance Survey Design", "Android Survey SDK"]


def test_fills_remaining_slots_once_every_section_is_represented():
    docs = [
        Doc("Advance Survey Design", "/product-guide/survey-designer/advance", size_bytes=9000),
        Doc("Branching Logic", "/product-guide/survey-designer/branching", size_bytes=8000),
        Doc("Android Survey SDK", "/product-guide/installation/android", size_bytes=1000),
    ]
    assert len(choose(docs, limit=3)) == 3


def test_drops_titles_that_name_the_container():
    docs = [Doc("Introduction", "/product-guide/introduction"), Doc("Bulk Email Surveys", "/a/b/c")]
    assert [s.label for s in choose(docs)] == ["Bulk Email Surveys"]


def test_drops_titles_too_long_to_read_as_a_chip():
    long_title = "A" * (MAX_LABEL_CHARS + 1)
    assert choose([Doc(long_title, "/a/b/c")]) == ()


def test_question_is_the_title_verbatim():
    # Nothing is conjugated, prefixed or rephrased. A generated question that
    # reads badly is worse than a topic that reads plainly.
    (starter,) = choose([Doc("Adding Hyperlink to Survey Text", "/product-guide/faq/hyperlink")])
    assert starter.question == "Adding Hyperlink to Survey Text"


def test_is_deterministic():
    docs = [Doc(f"Page {i}", f"/g/s{i}/p", size_bytes=100) for i in range(10)]
    assert choose(docs) == choose(list(reversed(docs)))


def test_a_single_segment_path_belongs_to_no_section():
    docs = [Doc("Pricing", "/pricing", size_bytes=5000), Doc("Billing", "/billing", size_bytes=4000)]
    # Neither is in a section, so neither excludes the other.
    assert len(choose(docs, limit=2)) == 2


def test_weights_beat_a_missing_size():
    # A crawled corpus reports no size at all, so without weights every
    # document ties and the chips become the first titles in the alphabet.
    docs = [
        Doc("Analysis", "/g/videos/analysis", size_bytes=None),
        Doc("Getting Started", "/g/videos/start", size_bytes=None),
    ]
    docs[0].doc_id, docs[1].doc_id = "a", "b"
    picked = [s.label for s in choose(docs, limit=1, weights={"a": 1, "b": 40})]
    assert picked == ["Getting Started"]


def test_path_beats_space_as_the_section():
    # Every page of a crawled site carries the same space, so a space-first
    # rule would treat the whole corpus as one section and pick one chip.
    docs = [
        Doc("Branching", "/g/survey-designer/branching", space="QWARY_HELP"),
        Doc("Android SDK", "/g/installation/android", space="QWARY_HELP"),
    ]
    assert len(choose(docs, limit=2)) == 2
    assert {s.section for s in choose(docs, limit=2)} == {"survey-designer", "installation"}
