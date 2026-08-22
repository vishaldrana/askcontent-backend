"""Architectural rules asserted by test rather than by document."""

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "askcontent"


def test_the_services_layer_imports_no_adapter():
    """CNT-FED-05 — swapping a mock for a real adapter must require no change
    outside the adapter directory."""
    offenders = []
    for path in (SRC / "services").rglob("*.py"):
        text = path.read_text()
        for match in re.finditer(r"^\s*from\s+\S*adapters\S*\s+import|^\s*import\s+\S*adapters", text, re.M):
            # The passage service legitimately calls the parser registry, which
            # is itself a port-shaped façade. Everything else is a violation.
            line = text[match.start():match.end()]
            if "parsers.registry" in line or "embedders.hashing" in line:
                continue
            offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, offenders


def test_parsing_libraries_are_confined_to_the_adapter_directory():
    """CNT-PAR-05 — the vendor-isolation rule, applied to parsers."""
    banned = ("trafilatura", "pypdfium2", "docling", "selectolax", "rapidocr", "puremagic")
    offenders = []
    for path in SRC.rglob("*.py"):
        if "adapters/parsers" in path.as_posix():
            continue
        text = path.read_text()
        for library in banned:
            if re.search(rf"^\s*(from|import)\s+{library}\b", text, re.M):
                offenders.append(f"{path.relative_to(SRC)}: {library}")
    assert not offenders, offenders


def test_no_agpl_or_revenue_capped_parser_is_imported():
    """CNT-PAR-08 — PyMuPDF is the best PDF extractor available and is AGPL."""
    banned = ("fitz", "pymupdf", "marker", "surya", "magic_pdf")
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for library in banned:
            if re.search(rf"^\s*(from|import)\s+{library}\b", text, re.M):
                offenders.append(f"{path.relative_to(SRC)}: {library}")
    assert not offenders, offenders


def test_every_mock_states_how_to_replace_it():
    """CNT-FED-04 — a mock that does not say how to replace it is an obstacle,
    not a scaffold."""
    for name in ("adapters/index/mock_pgp.py", "adapters/repository/mock_ecm.py"):
        text = (SRC / name).read_text()
        assert "REAL CALL" in text, name
        assert "OPEN Q" in text, name


def test_classification_makes_no_model_call():
    """CNT-PAR-06 / ARC-TEC-16 — a pure function of data we already hold."""
    text = (SRC / "domain" / "catalog.py").read_text()
    for forbidden in ("openai", "anthropic", "requests", "httpx", "embed"):
        assert forbidden not in text.lower()
