"""Content sniffing (CNT-PAR-03).

Format is determined by content, never by file extension and never by a
Content-Type header alone. Uploads and web sources both lie routinely: a `.pdf`
that is actually HTML, and an `application/octet-stream` that is a perfectly
good PDF, are both common.
"""

from __future__ import annotations

PDF_MAGIC = b"%PDF-"
HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<div", b"<p>")


def sniff(blob: bytes, declared_mime: str | None = None) -> str:
    head = blob[:2048].lstrip()

    if blob[:5] == PDF_MAGIC or PDF_MAGIC in blob[:1024]:
        return "application/pdf"

    lowered = head.lower()
    if any(marker in lowered for marker in HTML_MARKERS):
        return "text/html"

    # puremagic is a better general sniffer; it is optional so that the core
    # path has no hard dependency on it.
    try:
        import puremagic

        guesses = puremagic.magic_string(blob[:4096])
        if guesses:
            mime = guesses[0].mime_type
            if mime in ("application/pdf", "text/html"):
                return mime
    except Exception:  # noqa: BLE001 - sniffing must never raise
        pass

    if declared_mime in ("application/pdf", "text/html"):
        return declared_mime

    try:
        blob[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"
