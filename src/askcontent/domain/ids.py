"""Stable identifier construction.

Ids are content-derived rather than random so that a re-ingest of unchanged
material produces byte-identical ids (CNT-CHK-05, CNT-CHK-06). Citations stored
in a conversation therefore survive re-parsing, which is the whole point.
"""

from __future__ import annotations

import hashlib


def _digest(*parts: str, length: int = 16) -> str:
    h = hashlib.blake2b(digest_size=32)
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")  # unit separator: "a|b" and "ab" must not collide
    return h.hexdigest()[:length]


def scope_id(connector_id: str, canonical_scope_json: str) -> str:
    return "scp_" + _digest(connector_id, canonical_scope_json)


def chunk_id(doc_id: str, heading_path: tuple[str, ...], ordinal: int, text: str) -> str:
    """Stable across re-ingest while content and heading path are unchanged.

    Deliberately *excludes* parser and chunker versions: a parser upgrade that
    yields identical text and structure must not rotate citation ids. The
    embedding hash (CNT-PAR-13) is where those versions belong.
    """
    return "chk_" + _digest(doc_id, "/".join(heading_path), str(ordinal), text)


def text_hash(text: str, parser_id: str, parser_version: str, chunker_version: str) -> str:
    """Keys the embedding cache.

    Including parser and chunker versions is load-bearing: without them a
    parser upgrade produces better text under an unchanged hash and the
    improvement is never embedded (CNT-PAR-14).
    """
    return _digest(text, parser_id, parser_version, chunker_version, length=32)


def file_hash(blob: bytes) -> str:
    return hashlib.blake2b(blob, digest_size=16).hexdigest()


def plan_hash(canonical_spec_json: str, reranker_id: str, reranker_version: str) -> str:
    """CNT-RET-16 and CNT-RNK-06 — changing the reranker invalidates plans."""
    return "pln_" + _digest(canonical_spec_json, reranker_id, reranker_version)
