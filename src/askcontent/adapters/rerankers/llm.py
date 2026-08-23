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
SHORTLIST = 14

#: Passages are truncated before scoring. Relevance is decided in the first
#: sentence or two; the rest is tokens the model must read before it can
#: answer, and every one of them is latency the reader waits through.
MAX_CHARS = 420

_SYSTEM = """\
You score how well each passage answers a question.

Output exactly one line of `index:score` pairs, one per passage, separated by \
commas, nothing else. For five passages: 0:8,1:0,2:3,3:10,4:1

Keep the index the passage was given. No JSON, no explanation, no trailing \
text. Every passage must appear exactly once.

The scale:
  10  directly and completely answers the question
   7  contains most of the answer, or answers it for a common case
   4  is about the right subject but does not answer the question
   1  mentions a word from the question and is otherwise unrelated
   0  is navigation, boilerplate, a heading with no content, or off-topic

Judge only whether the passage answers THIS question. Do not reward length, \
confidence, or how well written it is. A short exact answer outranks a long \
adjacent one."""


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

        raw = reply.content if isinstance(reply.content, str) else str(reply.content)
        pairs = _scores_of(raw)

        # Carrying the index rather than relying on position is what makes a
        # short reply safe. A bare list has to be counted to be trusted, and a
        # model that drops one entry silently shifts every score after it onto
        # the wrong passage — which is worse than no score, because it promotes
        # something arbitrary with full confidence.
        out: dict[int, float] = {}
        for i, value in pairs.items():
            if 0 <= i < len(batch):
                # Normalised to 0..1 because the retrieval config's floor is
                # tuned against that scale, and a silently different range
                # would quietly change what gets cited.
                out[offset + i] = max(0.0, min(1.0, value / 10.0))

        if not out:
            raise ValueError(f"no usable scores in {raw[:120]!r}")
        return out


def _scores_of(raw: str) -> dict[int, float]:
    """`index:score` pairs, wherever in the reply they appear.

    Asking for pairs rather than a JSON object per passage is the difference
    between roughly sixty output tokens and two hundred, and output tokens are
    what a model's latency is made of. Asking for pairs rather than a bare list
    is what keeps a dropped entry from shifting every score onto the wrong
    passage. Models still occasionally wrap the line in a fence or a sentence,
    so the parse looks for the pattern rather than trusting the shape.
    """
    return {
        int(index): float(score)
        for index, score in re.findall(r"(\d+)\s*:\s*(\d+(?:\.\d+)?)", raw)
    }
