"""Live values: when they are fetched, what is kept, and what happens when not."""

import asyncio
import datetime as dt
from collections.abc import AsyncIterator

import pytest

from askcontent.domain.datapoint import (
    ContextSource,
    Datapoint,
    DatapointSet,
    FieldMap,
    extract,
    should_fetch,
)
from askcontent.ports.answerer import AnswerChunk
from askcontent.ports.context_source import Fetched
from askcontent.services import live_context
from askcontent.services.answering import AnsweringService

NOW = dt.datetime(2026, 8, 23, 12, 0, tzinfo=dt.timezone.utc)

SOURCE = ContextSource(
    name="Survey analytics",
    url="https://host.example.com/api/analysis/{key}",
    fields=(FieldMap(path="summary.nps", label="NPS"),
            FieldMap(path="summary.responses", label="Responses")),
    key_pattern=r"srv_[0-9a-f]{8}",
    ttl_seconds=60,
)


# ------------------------------------------------------------- routing ----

def test_no_key_means_no_call():
    # There is nothing to fetch *with*, and calling anyway asks the host's API
    # a question about nobody.
    assert not should_fetch("why did my NPS drop", has_key=False, corpus_covers=False).fetch


def test_a_question_about_the_readers_own_view_fetches():
    assert should_fetch("why did my NPS drop", has_key=True, corpus_covers=True).fetch
    assert should_fetch("what does this chart show", has_key=True, corpus_covers=True).fetch


def test_a_documented_question_does_not_fetch():
    # The corpus covers it and costs nothing extra, so the network call is not
    # in the path of a question the help centre already answers.
    verdict = should_fetch("how is NPS calculated", has_key=True, corpus_covers=True)
    assert not verdict.fetch
    assert "covers it" in verdict.why


def test_an_uncovered_question_fetches_even_without_a_pronoun():
    assert should_fetch("response rate", has_key=True, corpus_covers=False).fetch


# ------------------------------------------------------------ mapping ----

def test_only_mapped_fields_are_kept():
    payload = {"summary": {"nps": 42, "responses": 1284, "internal_cost_cents": 991},
               "debug": {"token": "secret"}}
    points = extract(payload, SOURCE.fields, source="S", key="k",
                     fetched_at=NOW, ttl_seconds=60)
    assert [p.label for p in points] == ["NPS", "Responses"]
    assert [p.value for p in points] == ["42", "1,284"]
    # The unmapped fields never reach the prompt, which is the whole point of
    # asking somebody to name them.
    rendered = DatapointSet(source="S", key="k", points=points).render()
    assert "secret" not in rendered and "991" not in rendered


def test_a_missing_field_is_skipped_not_rendered_as_none():
    points = extract({"summary": {"nps": 42}}, SOURCE.fields, source="S", key="k",
                     fetched_at=NOW, ttl_seconds=60)
    assert [p.label for p in points] == ["NPS"]
    assert points[0].number == 1


def test_numbers_are_positions_so_markers_line_up():
    points = extract({"summary": {"responses": 7}}, SOURCE.fields, source="S", key="k",
                     fetched_at=NOW, ttl_seconds=60)
    # NPS was absent, so Responses is [d1] — not [d2]. A marker naming a
    # position that was never offered is treated as fabricated.
    assert points[0].render().startswith("[d1] Responses")


def test_staleness_is_measured_from_when_it_was_read():
    point = Datapoint(number=1, source="S", key="k", label="NPS", value="42",
                      fetched_at=NOW, ttl_seconds=60)
    assert not point.is_stale(NOW + dt.timedelta(seconds=30))
    assert point.is_stale(NOW + dt.timedelta(seconds=90))


# -------------------------------------------------------------- fetch ----

class _Stub:
    def __init__(self, result: Fetched) -> None:
        self.result, self.calls = result, []

    def fetch(self, url, *, method="GET", headers=None, timeout_seconds=3.0):
        self.calls.append((url, headers or {}))
        return self.result


@pytest.fixture(autouse=True)
def _clear_cache():
    live_context.CACHE.clear()
    yield
    live_context.CACHE.clear()


def _read(stub, **kwargs):
    return live_context.read(
        SOURCE, connector_id="cn-x", question="why did my NPS drop",
        key="srv_8f2a11c4", corpus_covers=False, fetcher=stub, **kwargs,
    )


def test_the_key_goes_in_the_url_and_the_visitors_token_goes_with_it():
    stub = _Stub(Fetched(ok=True, payload={"summary": {"nps": 42, "responses": 3}}))
    result = _read(stub, visitor_token="Bearer abc")
    url, headers = stub.calls[0]
    assert url == "https://host.example.com/api/analysis/srv_8f2a11c4"
    # The host applies the host's access rules, which is the only place they
    # are known.
    assert headers["authorization"] == "Bearer abc"
    assert result.usable


def test_a_key_that_does_not_match_the_pattern_is_never_fetched():
    stub = _Stub(Fetched(ok=True, payload={}))
    result = live_context.read(
        SOURCE, connector_id="cn-x", question="why did my NPS drop",
        key="../../etc/passwd", corpus_covers=False, fetcher=stub,
    )
    assert result is None
    assert stub.calls == []


def test_a_failure_comes_back_as_a_value_that_can_be_told_to_the_reader():
    stub = _Stub(Fetched(ok=False, error="the source did not answer within 3s"))
    result = _read(stub)
    assert result is not None
    assert not result.usable
    assert "did not respond" in result.notice()


def test_a_failure_is_not_cached():
    # A cached timeout turns one bad second into a minute of them.
    stub = _Stub(Fetched(ok=False, error="boom"))
    _read(stub)
    _read(stub)
    assert len(stub.calls) == 2


def test_a_success_is_cached_for_the_ttl():
    stub = _Stub(Fetched(ok=True, payload={"summary": {"nps": 42, "responses": 3}}))
    first, second = _read(stub), _read(stub)
    assert len(stub.calls) == 1
    assert not first.cached and second.cached


def test_a_source_that_no_longer_validates_is_off_rather_than_half_on():
    assert live_context.parse({"name": "S"}) is None          # no url, no fields
    assert live_context.parse({}) is None
    assert live_context.parse(None) is None
    assert live_context.parse(SOURCE.model_dump(mode="json")) is not None


def test_a_disabled_source_is_not_read():
    off = SOURCE.model_copy(update={"enabled": False}).model_dump(mode="json")
    assert live_context.parse(off) is None


# -------------------------------------------------------- answering ----

class _Fake:
    name = model_id = "fake"

    def __init__(self, text, cited=(), used_data=()):
        self._text, self._cited, self._used_data = text, cited, used_data

    async def stream(self, *, question, passages, history=(), instructions="",
                     page=None, data=None) -> AsyncIterator[AnswerChunk]:
        yield AnswerChunk(text=self._text)
        yield AnswerChunk(done=True, supported=True, cited=self._cited,
                          used_data=self._used_data)


class _Citation:
    title = "Survey design"
    url = "https://help.example.com/nps"
    span = "Net Promoter Score is calculated from the 0-10 recommendation question."
    heading_path = ()
    updated_at = None
    authority = None


def _answer(answerer, question, citations, data=None):
    async def go():
        said, outcome = "", None
        async for text, result in AnsweringService(answerer).stream(
            question, citations, data=data
        ):
            said += text
            outcome = result or outcome
        return said, outcome

    return asyncio.run(go())


def _set(n=2):
    points = extract({"summary": {"nps": 42, "responses": 1284}}, SOURCE.fields,
                     source="Survey analytics", key="srv_8f2a11c4",
                     fetched_at=NOW, ttl_seconds=60)[:n]
    return DatapointSet(source="Survey analytics", key="srv_8f2a11c4", points=points)


def test_a_datapoint_marker_counts_as_attribution():
    answerer = _Fake("Your NPS is 42 [d1].", used_data=(1,))
    _, outcome = _answer(answerer, "what is my NPS", [_Citation()], data=_set())
    assert outcome.supported
    assert outcome.used_data == (1,)


def test_a_marker_naming_a_value_that_was_never_supplied_is_rejected():
    # The same defect as citing passage 9 of 4, and the same answer.
    answerer = _Fake("Your churn is 4% [d7].", used_data=(7,))
    _, outcome = _answer(answerer, "what is my NPS", [_Citation()], data=_set())
    assert not outcome.supported
    assert "d7" in outcome.reason


def test_live_values_count_towards_coverage():
    answerer = _Fake("1,284 responses so far [d2].", used_data=(2,))
    _, outcome = _answer(answerer, "how many responses do I have", [], data=_set())
    assert outcome.supported


def test_the_refusal_names_every_place_it_looked():
    answerer = _Fake("", used_data=())
    said, outcome = _answer(
        answerer, "what is our parental leave policy", [_Citation()], data=_set()
    )
    assert not outcome.supported
    assert "live figures" in said
