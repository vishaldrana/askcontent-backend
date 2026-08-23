"""Embeddings from a real model, via LangChain.

WHY THIS MATTERS MORE THAN IT LOOKS
===================================
`HashingEmbedder` is a hashed character-n-gram bag. It is deterministic, needs
no network and is exactly right for tests — and it cannot do the one thing
retrieval is for. Asked "what is this product about", it can only match
documents that repeat the words of the question; the overview page that answers
it says "experience management platform" and shares almost no vocabulary, so it
never surfaces. Every symptom that looks like bad ranking on a broad question
is this.

Configured the same way askdb configures it, so one deployment sets both:
EMBEDDING_PROVIDER, EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_DIM.

**Changing the model invalidates the index.** Vectors from two models are not
comparable — cosine distance between them is a number with no meaning, and the
failure is silent: retrieval keeps working and quietly returns nonsense. The
model id is therefore reported as `model_id` and recorded with what was
indexed, so a mismatch is a detectable state rather than a mystery.
"""

from __future__ import annotations


class OpenAIEmbedder:
    """`Embedder` over any LangChain embeddings provider."""

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        dimension: int = 1536,
        batch_size: int = 256,
    ) -> None:
        self.model_id = model
        self.dimension = dimension
        self._batch = batch_size

        if provider != "openai":
            raise ValueError(
                f"embedding provider '{provider}' is not wired here yet; the "
                f"LangChain class for it goes in this file, behind the same port"
            )

        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "langchain-openai is not installed — "
                "pip install 'askcontent-backend[langchain-openai]'"
            ) from exc

        kwargs: dict = {"model": model}
        if api_key:
            kwargs["api_key"] = api_key
        # text-embedding-3-* support Matryoshka truncation, so the stored width
        # is a choice rather than a property of the model. Asking for it
        # explicitly keeps the column, the index and the vectors in agreement.
        if dimension:
            kwargs["dimensions"] = dimension

        self._client = OpenAIEmbeddings(**kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Batched because a corpus is indexed in one pass and one request per
        # chunk turns a minute into an hour. Empty strings are replaced rather
        # than sent: the API rejects them, and one bad chunk failing a whole
        # batch is a re-index nobody can complete.
        out: list[list[float]] = []
        cleaned = [t if t.strip() else " " for t in texts]
        for start in range(0, len(cleaned), self._batch):
            out.extend(self._client.embed_documents(cleaned[start : start + self._batch]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text if text.strip() else " ")
