"""
Retrieve: search the CloudServe documentation corpus.

Build spec requirements:
- Searches the supplied documentation corpus, returns ranked passages with scores (A4)
- Returned identifiers resolve to the real corpus (so citations can be verified)
- Applies a relevance threshold and returns nothing rather than something irrelevant
- Chunking strategy is a decision to make, justify and explain

Chunking decision (see docs/architecture.md for the full rationale): each
doc's `content` is split by markdown heading (##) rather than by fixed
character count. Dataset_Guide.docx explicitly warns that "splitting inside
a resolution sequence tends to produce passages that retrieve well but read
as incomplete" -- heading-aware chunking keeps a numbered resolution list
intact as one chunk instead of severing it mid-sequence.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

from src.config import SETTINGS
from src.schemas import RetrievalResult, RetrievedPassage

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"\n(?=#{1,3}\s)")
_COLLECTION_NAME = "cloudserve_docs"


def _chunk_document(doc: dict) -> list[str]:
    content = doc.get("content", "") or ""
    if not content.strip():
        return []
    parts = [p.strip() for p in _HEADING_RE.split(content) if p.strip()]
    return parts or [content.strip()]


class Retriever:
    def __init__(self, chroma_path: str | None = None, collection_name: str = _COLLECTION_NAME):
        self._path = chroma_path or SETTINGS.chroma_path
        Path(self._path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._path)
        self._embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=SETTINGS.embedding_model
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name, embedding_function=self._embed_fn
        )

    def is_indexed(self) -> bool:
        return self._collection.count() > 0

    def index_documentation(self, documentation_path: str, force: bool = False) -> int:
        """Build the vector index from documentation.json. Idempotent unless force=True."""
        if self.is_indexed() and not force:
            logger.info("Retriever already indexed (%d chunks); skipping.", self._collection.count())
            return self._collection.count()

        if force:
            existing = self._collection.get()
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])

        docs = json.loads(Path(documentation_path).read_text())
        ids, texts, metadatas = [], [], []
        for doc in docs:
            chunks = _chunk_document(doc)
            for i, chunk in enumerate(chunks):
                ids.append(f"{doc['doc_id']}::chunk{i}")
                texts.append(chunk)
                metadatas.append(
                    {
                        "doc_id": doc["doc_id"],
                        "title": doc.get("title", ""),
                        "category": doc.get("category", ""),
                    }
                )

        if not ids:
            logger.warning("No chunks produced from %s", documentation_path)
            return 0

        self._collection.add(ids=ids, documents=texts, metadatas=metadatas)
        logger.info("Indexed %d chunks from %d documents.", len(ids), len(docs))
        return len(ids)

    def retrieve(self, query: str, top_k: int | None = None,
                 min_score: float | None = None) -> RetrievalResult:
        top_k = top_k or SETTINGS.retrieval_top_k
        min_score = SETTINGS.retrieval_min_score if min_score is None else min_score

        if not query.strip() or not self.is_indexed():
            return RetrievalResult(passages=[], query=query)

        results = self._collection.query(query_texts=[query], n_results=top_k)
        passages: list[RetrievedPassage] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for _id, text, meta, dist in zip(ids, docs, metas, dists):
            # Chroma's default distance is cosine distance in [0, 2]; convert
            # to a similarity score in [0, 1] so the relevance threshold in
            # .env (RETRIEVAL_MIN_SCORE) reads as "how relevant", not "how far".
            score = max(0.0, 1.0 - (dist / 2.0))
            if score < min_score:
                continue
            passages.append(
                RetrievedPassage(
                    doc_id=meta["doc_id"],
                    title=meta.get("title", ""),
                    chunk_text=text,
                    score=round(score, 4),
                )
            )

        return RetrievalResult(passages=passages, query=query)


class FakeRetriever:
    """Deterministic stand-in for tests and offline development -- mirrors
    FakeChatClient in src/llm_client.py. Returns `fixed_passages` for every
    query unless `by_query_substring` matches, so pipeline/router/guardrail
    logic can be exercised without chromadb or a downloaded embedding model.
    """

    def __init__(
        self,
        fixed_passages: list[RetrievedPassage] | None = None,
        by_query_substring: dict[str, list[RetrievedPassage]] | None = None,
    ):
        self.fixed_passages = fixed_passages or []
        self.by_query_substring = by_query_substring or {}

    def is_indexed(self) -> bool:
        return True

    def index_documentation(self, documentation_path: str, force: bool = False) -> int:
        return 0

    def retrieve(self, query: str, top_k: int | None = None, min_score: float | None = None) -> RetrievalResult:
        for needle, passages in self.by_query_substring.items():
            if needle in query:
                return RetrievalResult(passages=passages, query=query)
        return RetrievalResult(passages=self.fixed_passages, query=query)
