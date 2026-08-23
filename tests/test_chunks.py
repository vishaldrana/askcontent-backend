"""Chunking — the unit retrieval actually returns."""

from askcontent.adapters.parsers.html import HtmlParser
from askcontent.domain.chunks import chunk_document
from askcontent.domain.documents import ParseHints


def parse(html: str):
    return HtmlParser().parse(
        "doc", html.encode(), mime="text/html", hints=ParseHints()
    )



def test_a_numbered_step_heading_does_not_become_its_own_chunk():
    """Help content numbers its steps as headings. Chunking each into its own
    section produces fragments like "Pass your URL" — which retrieve badly and
    answer nothing, while the procedure they came from sits in the corpus
    unassembled."""
    html = """<html><body>
      <h1>Adding a hyperlink</h1>
      <p>Guide to adding an anchor tag.</p>
      <h3>1. Go to Builder</h3><p>Open the builder.</p>
      <h3>2. Select text</h3><p>Select the word to link.</p>
      <h3>3. Click Insert Link</h3><p>The dialog opens.</p>
      <h3>4. Type the URL</h3><p>Pass your URL.</p>
    </body></html>"""

    parsed = parse(html)
    chunks = chunk_document(parsed)

    # Every step in one retrievable passage, not four fragments.
    assert len(chunks) <= 2
    body = " ".join(c.text for c in chunks)
    for step in ("Go to Builder", "Select text", "Click Insert Link", "Type the URL"):
        assert step in body


def test_ordinals_are_renumbered_after_merging():
    """A citation naming chunk 7 of 4 is a puzzle."""
    html = "<html><body>" + "".join(
        f"<h3>{i}. Step</h3><p>Do the thing.</p>" for i in range(6)
    ) + "</body></html>"
    parsed = parse(html)
    chunks = chunk_document(parsed)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
