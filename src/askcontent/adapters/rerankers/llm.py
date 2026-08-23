"""Reranking with a language model.

╔══════════════════════════════════════════════════════════════════════════╗
║  TEMPORARY. This is a stand-in for a cross-encoder and is meant to be     ║
║  deleted.                                                                 ║
║                                                                           ║
║  Replace with `cross_encoder.py` (BAAI/bge-reranker-v2-m3 or equivalent)  ║
║  as soon as the model can be baked into the image, or with a hosted       ║
║  rerank endpoint when one is available. Nothing outside this file needs   ║
║  to change: it implements the same `Reranker` port, and the swap is a     ║
║  line in `build_reranker`.                                                ║
║                                                                           ║
║  Why it must go:                                                          ║
║    · every query costs a model call, on the hot path, with a network      ║
║      round trip inside the user's wait;                                   ║
║    · a cross-encoder is *better* at this — it is trained for exactly      ║
║      this pairwise judgement, and it is deterministic in a way a          ║
║      generative model only approximates;                                  ║
║    · a rate limit or an outage degrades ranking rather than failing       ║
║      loudly, and degradation is the failure mode nobody notices.          ║
╚══════════════════════════════════════════════════════════════════════════╝

Why it is here anyway: the alternatives available right now are a lexical
overlap count, which ranks "Turn off Qwary Branding" above the overview page
for "what is Qwary about", and a bi-encoder, which cannot attend across the
question and the passage together and so scores near-duplicates identically.
Both are visibly worse than a model that reads the pair and judges it.

The prompt asks for a *relevance* judgement, not a summary or an answer. It
sees passage text only — never the answer, never the other scores — so it
cannot be talked into promoting something by its framing.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("askcontent.rerank")

#: One call per this many passages. Large enough to amortise the round trip,
#: small enough that a single malformed reply costs part of a ranking rather
#: than all of it.
BATCH = 20

#: How many passages the model actually sees.
#:
#: A cascade, and the reason is latency rather than money. Reranking sixty
#: passages meant three sequential model calls inside the user's wait, which
#: took a single question past twenty seconds. The cheap ranker is good enough
#: to decide which twenty are worth a careful look, and wrong about the order
#: within them — which is exactly the division of labour a cascade is for.
#:
#: Everything below the shortlist keeps its cheap score, scaled beneath the
#: model's lowest, so the tail stays ordered instead of collapsing.
SHORTLIST = 20

#: Passages are truncated before scoring. Relevance is decided in the first
#: sentence or two; sending four thousand characters buys nothing and pushes
#: the batch into a bigger, slower request.
MAX_CHARS = 700

_SYSTEM = """\
You score how well each passage answers a question. Reply with JSON only.

Output: {"scores": [{"i": <passage number>, "s": <0-10>}, ...]} with one entry \
for every passage you were given, in any order.

The scale:
  10  directly and completely answers the question
   7  contains most of the answer, or answers it for a common case
   4  is about the right subject but does not answer the question
   1  mentions a word from the question and is otherwise unrelated
   0  is navigation, boilerplate, a heading with no content, or off-topic

Judge only whether the passage answers THIS question. Do not reward length, \
confidence, or how well written it is. A short exact answer outranks a long \
adjacent one. Do not explain. JSON only."""


class LlmReranker:
    reranker_id = "llm-rerank-temporary"

    def __init__(self, *, provider: str = "openai", model: str = "gpt-4.1-mini",
                 api_key: str | None = None, fallback=None) -> None:
        from langchain.chat_models import init_chat_model

        self.reranker_version = model
        # A model that reads the pair scores unrelated text near zero, so the
        # floor sits where "about the right subject but not an answer" lands.
        self.score_floor = 0.35
        self._fallback = fallback

        kwargs: dict = {"model": model, "model_provider": provider, "temperature": 0}
        if api_key:
            kwargs["api_key"] = api_key
        self._model = init_chat_model(**kwargs)

        logger.warning(
            "reranker: using the TEMPORARY llm reranker (%s). This costs a "
            "model call per query and is to be replaced by a cross-encoder — "
            "see adapters/rerankers/llm.py",
            model,
        )

    def rerank(self, question: str, texts: list[str]) -> list:
        from ...ports.reranker import RerankResult

        if not texts:
            return []

        # First stage: the cheap ranker decides what deserves a careful look.
        shortlist = list(range(len(texts)))
        if self._fallback is not None and len(texts) > SHORTLIST:
            ranked = self._fallback.rerank(question, texts)
            shortlist = sorted(r.index for r in ranked[:SHORTLIST])

        scores: dict[int, float] = {}
        for start in range(0, len(shortlist), BATCH):
            window = shortlist[start : start + BATCH]
            try:
                raw = self._score(question, [texts[i] for i in window], 0)
            except Exception as exc:  # noqa: BLE001
                # A failed batch must not silently sink its passages to the
                # bottom, which is what a default of zero would do. They are
                # left unscored and placed below by the fallback ordering.
                logger.warning("llm rerank batch failed (%s); falling back", exc)
                continue
            for local, score in raw.items():
                scores[window[local]] = score

        missing = [i for i in range(len(texts)) if i not in scores]
        if missing:
            for index, score in self._recover(question, texts, missing, scores).items():
                scores[index] = score

        out = [
            RerankResult(index=i, score=scores.get(i, 0.0))
            for i in range(len(texts))
        ]
        out.sort(key=lambda r: -r.score)
        return out

    def _recover(
        self, question: str, texts: list[str], missing: list[int],
        scored: dict[int, float],
    ) -> dict[int, float]:
        """Place passages the model did not score, without corrupting the scale.

        The obvious move — score them with the fallback and merge — is wrong.
        Cosine similarity mapped to 0..1 sits around 0.8 for anything vaguely
        related, while a model that read the pair and judged it irrelevant says
        0.1. Merging the two puts every unscored passage above every genuinely
        low-scoring one, which is the opposite of what either signal said.

        So the fallback is used for *order* only, and that order is laid out
        beneath the lowest score the model gave. A passage the model declined
        to mention is, by its own account, not among the ones worth mentioning.
        """
        if self._fallback is None:
            return dict.fromkeys(missing, 0.0)

        ordered = [
            missing[result.index]
            for result in self._fallback.rerank(question, [texts[i] for i in missing])
        ]
        ceiling = min(scored.values()) if scored else 0.0
        step = ceiling / (len(ordered) + 1) if ceiling > 0 else 0.0
        return {index: ceiling - step * (rank + 1) for rank, index in enumerate(ordered)}

    def _score(self, question: str, batch: list[str], offset: int) -> dict[int, float]:
        from langchain_core.messages import HumanMessage, SystemMessage

        listing = "\n\n".join(
            f"[{i}] {text[:MAX_CHARS]}" for i, text in enumerate(batch)
        )
        reply = self._model.invoke([
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=f"QUESTION\n{question}\n\nPASSAGES\n{listing}"),
        ])

        payload = _json_of(reply.content if isinstance(reply.content, str) else str(reply.content))
        out: dict[int, float] = {}
        for entry in payload.get("scores", []):
            try:
                i, s = int(entry["i"]), float(entry["s"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= i < len(batch):
                # Normalised to 0..1 because the retrieval config's floor is
                # tuned against that scale, and a silently different range
                # would quietly change what gets cited.
                out[offset + i] = max(0.0, min(1.0, s / 10.0))
        return out


def _json_of(raw: str) -> dict:
    """Models fence JSON in markdown about a third of the time."""
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1).strip()
    return json.loads(raw)
