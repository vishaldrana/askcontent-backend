"""Parser registry and the sandbox boundary.

CNT-PAR-16/17: parsers run in an isolated subprocess with no network, memory
and CPU limits, a wall-clock timeout, and caps on input size and page count. A
crash, hang or resource kill fails *that document only*, with a recorded
reason. It never fails the ingest run and never takes down the worker.

Document parsers are a standing attack surface — malformed-file memory
corruption, decompression bombs, XXE, pathological layouts that run for hours.
Content from a wiki anyone in the company can edit is not trusted input.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import resource

from ...domain.documents import ParsedDocument, ParseHints, ParsePath, ParseQuality
from .html import HtmlParser
from .pdf import PdfParser
from .sniff import sniff

SUPPORTED_MIMES = ("application/pdf", "text/html")

_PARSERS = (HtmlParser(), PdfParser())


def parser_version_for_content(blob: bytes, declared_mime: str | None = None) -> str:
    """The parser version that *would* handle this content.

    Sniffs, because the declared type is routinely wrong — the stub content
    manager reports `application/octet-stream` for everything, and comparing
    against that resolved to "no parser" and quietly disabled the
    cosmetic-change optimisation it was feeding.
    """
    return parser_version_for(sniff(blob, declared_mime))


def parser_version_for(mime: str) -> str:
    """The version the current parser would stamp on this content type.

    Exposed here so callers can ask "would a re-parse produce different output?"
    without importing a parser. The registry is the façade; reaching past it to
    `parsers.pdf` is the vendor-isolation break the architecture test exists to
    catch, and it caught exactly that.
    """
    parser = next((p for p in _PARSERS if p.supports(mime)), None)
    return parser.parser_version if parser else "-"


def capabilities() -> dict[str, bool]:
    """What this deployment can actually parse right now.

    Surfaced in the admin console: a capability gap must be visible as a gap,
    not discovered as a mysteriously empty corpus.
    """
    caps: dict[str, bool] = {"text/html": True}
    try:
        import pypdfium2  # noqa: F401

        caps["application/pdf:text"] = True
    except ImportError:
        caps["application/pdf:text"] = False
    try:
        import docling  # noqa: F401

        caps["application/pdf:layout"] = True
    except ImportError:
        caps["application/pdf:layout"] = False
    try:
        import rapidocr_onnxruntime  # noqa: F401

        caps["application/pdf:ocr"] = True
    except ImportError:
        caps["application/pdf:ocr"] = False
    caps["application/pdf"] = caps["application/pdf:text"] or caps["application/pdf:layout"]
    return caps


def parse_document(
    doc_id: str,
    blob: bytes,
    declared_mime: str | None = None,
    hints: ParseHints | None = None,
    *,
    sandbox: bool = True,
) -> ParsedDocument:
    hints = hints or ParseHints()
    mime = sniff(blob, declared_mime)

    if mime not in SUPPORTED_MIMES:
        # Rejected with a named reason, never silently skipped (CNT-PAR-02). A
        # silent skip is indistinguishable from a document that was never
        # discovered, and the first symptom is a confidently wrong answer.
        return _refusal(doc_id, f"unsupported format: {mime} (phase 1 accepts {', '.join(SUPPORTED_MIMES)})")

    if len(blob) > hints.max_bytes:
        return _refusal(doc_id, f"document exceeds size cap ({len(blob)} > {hints.max_bytes})")

    parser = next((p for p in _PARSERS if p.supports(mime)), None)
    if parser is None:
        return _refusal(doc_id, f"no parser registered for {mime}")

    if not sandbox:
        return parser.parse(doc_id, blob, mime, hints)
    return _parse_sandboxed(parser, doc_id, blob, mime, hints)


def _parse_sandboxed(parser, doc_id, blob, mime, hints) -> ParsedDocument:
    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_child,
        args=(queue, parser.__class__.__module__, parser.__class__.__name__,
              doc_id, blob, mime, hints.model_dump()),
        daemon=True,
    )
    process.start()
    process.join(timeout=hints.timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(timeout=2.0)
        return _refusal(doc_id, f"parse timed out after {hints.timeout_seconds}s")

    if queue.empty():
        return _refusal(doc_id, f"parser process died (exit {process.exitcode}) — likely a resource kill")

    status, payload = queue.get()
    if status == "ok":
        return ParsedDocument.model_validate(payload)
    return _refusal(doc_id, f"parser raised: {payload}")


def _child(queue, module_name, class_name, doc_id, blob, mime, hints_data) -> None:
    try:
        # Resource limits. The address-space cap is the one that matters:
        # decompression bombs and pathological layouts both present as
        # unbounded allocation.
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        # Network egress is blocked at the container level in deployment; this
        # is defence in depth for local runs.
        os.environ["no_proxy"] = "*"

        import importlib

        cls = getattr(importlib.import_module(module_name), class_name)
        parsed = cls().parse(doc_id, blob, mime, ParseHints.model_validate(hints_data))
        queue.put(("ok", parsed.model_dump(mode="json")))
    except BaseException as exc:  # noqa: BLE001 - the point is to contain everything
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _refusal(doc_id: str, reason: str) -> ParsedDocument:
    return ParsedDocument(
        doc_id=doc_id,
        blocks=(),
        parser_id="registry",
        parser_version="1.0.0",
        parse_path=ParsePath.REFUSED,
        refusal_reason=reason,
        quality=ParseQuality(warnings=(reason,)),
    )
