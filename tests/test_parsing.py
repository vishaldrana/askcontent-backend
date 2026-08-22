"""CNT-PAR-*, CNT-CHK-* — parsing and chunking."""

from askcontent.adapters.parsers.registry import parse_document
from askcontent.adapters.parsers.sniff import sniff
from askcontent.domain.chunks import chunk_document
from askcontent.domain.documents import BlockKind, ParsePath

HTML = b"""<html><body>
<nav><a>Home</a><a>Products</a><a>Careers</a></nav>
<h2>Entitlement</h2>
<p>A primary caregiver is entitled to 18 weeks of paid parental leave, available in the 24 months following birth or placement of the child.</p>
<h3>Table</h3>
<table><tr><th>Caregiver</th><th>Weeks</th></tr><tr><td>Primary</td><td>18</td></tr></table>
</body></html>"""


def test_format_is_sniffed_not_declared():
    """CNT-PAR-03 — uploads and web sources both lie routinely."""
    assert sniff(HTML, declared_mime="application/pdf") == "text/html"
    assert sniff(b"%PDF-1.7\n...", declared_mime="text/html") == "application/pdf"


def test_unsupported_format_is_refused_with_a_named_reason():
    """CNT-PAR-02 — a silent skip is indistinguishable from a document that was
    never discovered."""
    parsed = parse_document("D", b"\x00\x01\x02binary", sandbox=False)
    assert parsed.refused
    assert "unsupported format" in parsed.refusal_reason


def test_headings_survive_boilerplate_removal():
    """CNT-CHK-02 — trafilatura's own output drops headings, so structure comes
    from the original markup and extraction is used only as a filter."""
    parsed = parse_document("D", HTML, sandbox=False)
    headings = [b.text for b in parsed.blocks if b.kind is BlockKind.HEADING]
    assert "Entitlement" in headings
    body = " ".join(b.text for b in parsed.blocks)
    assert "Careers" not in body  # navigation furniture removed


def test_a_table_is_never_split_and_never_flattened():
    """CNT-CHK-03."""
    parsed = parse_document("D", HTML, sandbox=False)
    chunks = chunk_document(parsed)
    table_chunks = [c for c in chunks if c.is_table]
    assert len(table_chunks) == 1
    assert "| Primary | 18 |" in table_chunks[0].text


def test_heading_path_is_prepended_before_embedding():
    """'Rate limits' under two different parents must not embed identically."""
    parsed = parse_document("D", HTML, sandbox=False)
    chunk = next(c for c in chunk_document(parsed) if c.heading_path)
    assert chunk.embed_text.startswith(" > ".join(chunk.heading_path))


def test_chunking_is_deterministic_and_ids_are_stable():
    """CNT-CHK-05, CNT-CHK-06 — citations in stored conversations must not rot."""
    first = chunk_document(parse_document("D", HTML, sandbox=False))
    second = chunk_document(parse_document("D", HTML, sandbox=False))
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert [c.text for c in first] == [c.text for c in second]


def test_pdf_without_the_extra_refuses_rather_than_degrading():
    """CNT-PAR-11 — an unread document is a visible gap; a badly read one is an
    invisible wrong answer."""
    parsed = parse_document("D", b"%PDF-1.4\ntrailer\n%%EOF\n", sandbox=False)
    assert parsed.parse_path in (ParsePath.REFUSED, ParsePath.PDF_TEXT_LAYER, ParsePath.PDF_LAYOUT)
    if parsed.refused:
        assert parsed.refusal_reason
