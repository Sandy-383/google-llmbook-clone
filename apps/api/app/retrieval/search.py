"""Hybrid retrieval: vector similarity + keyword match, fused, then expanded with neighbours.

Vector search finds passages that mean the same thing; keyword search catches exact names,
numbers and jargon that embeddings blur. Reciprocal Rank Fusion combines the two rankings
without having to calibrate their scores against each other.
"""

import math
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Source

_RRF_K = 60
_MAX_CHUNKS_PER_PASSAGE = 3
# Lexemes that are safe to quote inside to_tsquery (letters, digits, '.', '-', '_').
_SAFE_LEXEME = re.compile(r"^[\w.\-]+$")


def prompt_tokens(text: str) -> int:
    """Conservative token estimate for prompt budgeting. Deliberately higher than the chunker's
    estimate: running past the context window makes Ollama silently drop the start of the
    prompt, which is where the instructions are."""
    return math.ceil(len(text) / 3.2)


@dataclass(frozen=True)
class _ChunkRow:
    id: int
    source_id: uuid.UUID
    ord: int
    text: str
    heading: str | None
    page_start: int | None
    page_end: int | None


@dataclass
class Passage:
    """One numbered source in the prompt: a hit, possibly merged with adjacent chunks."""

    number: int
    source_id: uuid.UUID
    source_title: str
    chunk_ids: list[int]
    page_start: int | None
    page_end: int | None
    heading: str | None
    text: str
    similarity: float | None

    def label(self) -> str:
        """e.g. "Employee Handbook, pp. 3-4" or "Notes › Setup"."""
        where = self.source_title
        if self.page_start is not None:
            pages = (
                f"p. {self.page_start}"
                if self.page_start == self.page_end
                else f"pp. {self.page_start}-{self.page_end}"
            )
            where = f"{where}, {pages}"
        elif self.heading:
            where = f"{where} › {self.heading}"
        return where

    def to_json(self) -> dict[str, object]:
        return {
            "number": self.number,
            "source_id": str(self.source_id),
            "source_title": self.source_title,
            "chunk_ids": self.chunk_ids,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "heading": self.heading,
            "text": self.text,
            "similarity": None if self.similarity is None else round(self.similarity, 4),
        }


@dataclass
class RetrievalResult:
    passages: list[Passage] = field(default_factory=list)
    # Best cosine similarity of any chunk to the query; low means the sources don't cover it.
    top_similarity: float | None = None


async def retrieve(
    db: AsyncSession,
    *,
    notebook_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    query: str,
    query_vector: list[float],
    candidates: int,
    top_k: int,
    token_budget: int,
) -> RetrievalResult:
    if not source_ids:
        return RetrievalResult()
    vector_hits = await _vector_search(db, notebook_id, source_ids, query_vector, candidates)
    keyword_hits = await _keyword_search(db, notebook_id, source_ids, query, candidates)

    similarity = dict(vector_hits)
    fused = reciprocal_rank_fusion([[cid for cid, _ in vector_hits], keyword_hits])[: top_k * 2]
    if not fused:
        return RetrievalResult(top_similarity=max(similarity.values(), default=None))

    hits = await _load_chunks(db, fused)
    neighbours = await _load_neighbours(db, hits.values())
    title_rows = await db.execute(select(Source.id, Source.title).where(Source.id.in_(source_ids)))
    titles = {source_id: title for source_id, title in title_rows}
    passages = build_passages(
        [hits[cid] for cid in fused if cid in hits],
        neighbours,
        titles,
        similarity,
        top_k=top_k,
        token_budget=token_budget,
    )
    return RetrievalResult(passages=passages, top_similarity=max(similarity.values(), default=None))


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = _RRF_K) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda item: scores[item], reverse=True)


async def _vector_search(
    db: AsyncSession,
    notebook_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    vector: list[float],
    limit: int,
) -> list[tuple[int, float]]:
    # Without iterative scans, HNSW returns the global nearest neighbours *then* filters by
    # notebook, which can leave a small notebook with too few (or no) results.
    await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = Chunk.embedding.cosine_distance(vector)
    rows = await db.execute(
        select(Chunk.id, distance)
        .where(Chunk.notebook_id == notebook_id, Chunk.source_id.in_(source_ids))
        .order_by(distance)
        .limit(limit)
    )
    # relaxed_order may return results slightly out of order.
    return sorted(((cid, 1.0 - float(d)) for cid, d in rows), key=lambda r: r[1], reverse=True)


async def _keyword_search(
    db: AsyncSession,
    notebook_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    query: str,
    limit: int,
) -> list[int]:
    lexemes = await db.scalar(select(func.tsvector_to_array(func.to_tsvector("english", query))))
    expression = or_tsquery(lexemes or [])
    if not expression:
        return []
    # OR rather than AND: a whole question rarely has every word in one passage. Cover-density
    # ranking still puts passages matching more (and closer) terms first.
    tsquery = func.to_tsquery("simple", expression)
    rows = await db.scalars(
        select(Chunk.id)
        .where(
            Chunk.notebook_id == notebook_id,
            Chunk.source_id.in_(source_ids),
            Chunk.tsv.op("@@")(tsquery),
        )
        .order_by(func.ts_rank_cd(Chunk.tsv, tsquery).desc())
        .limit(limit)
    )
    return list(rows)


def or_tsquery(lexemes: list[str]) -> str:
    """Already-stemmed lexemes joined with OR, quoted for to_tsquery('simple', ...)."""
    safe = sorted({lex for lex in lexemes if _SAFE_LEXEME.match(lex)})
    return " | ".join(f"'{lex}'" for lex in safe)


async def _load_chunks(db: AsyncSession, ids: list[int]) -> dict[int, _ChunkRow]:
    rows = await db.execute(select(*_COLUMNS).where(Chunk.id.in_(ids)))
    return {row.id: _ChunkRow(*row) for row in rows}


async def _load_neighbours(
    db: AsyncSession, hits: Iterable[_ChunkRow]
) -> dict[tuple[uuid.UUID, int], _ChunkRow]:
    keys = {(hit.source_id, hit.ord + offset) for hit in hits for offset in (-1, 1)}
    if not keys:
        return {}
    rows = await db.execute(
        select(*_COLUMNS).where(tuple_(Chunk.source_id, Chunk.ord).in_(list(keys)))
    )
    return {(row.source_id, row.ord): _ChunkRow(*row) for row in rows}


_COLUMNS = (
    Chunk.id,
    Chunk.source_id,
    Chunk.ord,
    Chunk.text,
    Chunk.heading,
    Chunk.page_start,
    Chunk.page_end,
)


def build_passages(
    ranked_hits: list[_ChunkRow],
    neighbours: dict[tuple[uuid.UUID, int], _ChunkRow],
    titles: dict[uuid.UUID, str],
    similarity: dict[int, float],
    *,
    top_k: int,
    token_budget: int,
) -> list[Passage]:
    """Pick hits in rank order within the budget, then add each hit's neighbours (answers often
    straddle a chunk boundary), then merge adjacent chunks into one numbered passage."""
    chosen: dict[tuple[uuid.UUID, int], tuple[int, _ChunkRow]] = {}  # key -> (rank, row)
    used = 0

    for rank, row in enumerate(ranked_hits):
        if len(chosen) >= top_k:
            break
        cost = prompt_tokens(row.text)
        if used + cost <= token_budget:
            chosen[(row.source_id, row.ord)] = (rank, row)
            used += cost

    for (source_id, ord_), (rank, _) in sorted(chosen.copy().items(), key=lambda kv: kv[1][0]):
        for key in ((source_id, ord_ - 1), (source_id, ord_ + 1)):
            if key in chosen or key not in neighbours:
                continue
            cost = prompt_tokens(neighbours[key].text)
            if used + cost <= token_budget:
                chosen[key] = (rank, neighbours[key])
                used += cost

    runs: list[list[tuple[int, _ChunkRow]]] = []
    for key in sorted(chosen, key=lambda k: (str(k[0]), k[1])):
        rank, row = chosen[key]
        previous = runs[-1][-1][1] if runs else None
        adjacent = (
            previous is not None
            and previous.source_id == row.source_id
            and previous.ord == row.ord - 1
        )
        # Long runs are split, so a citation points at a page or two, not at a whole chapter.
        if adjacent and len(runs[-1]) < _MAX_CHUNKS_PER_PASSAGE:
            runs[-1].append((rank, row))
        else:
            runs.append([(rank, row)])
    runs.sort(key=lambda run: min(rank for rank, _ in run))

    passages = []
    for number, run in enumerate(runs, start=1):
        rows = [row for _, row in run]
        pages = [p for r in rows for p in (r.page_start, r.page_end) if p is not None]
        sims = [similarity[r.id] for r in rows if r.id in similarity]
        passages.append(
            Passage(
                number=number,
                source_id=rows[0].source_id,
                source_title=titles.get(rows[0].source_id, "Untitled source"),
                chunk_ids=[r.id for r in rows],
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                heading=rows[0].heading,
                text=merge_overlapping([r.text for r in rows]),
                similarity=max(sims) if sims else None,
            )
        )
    return passages


def merge_overlapping(texts: list[str]) -> str:
    """Join consecutive chunks, dropping the overlap the chunker repeats at each boundary."""
    merged = texts[0]
    for nxt in texts[1:]:
        overlap = 0
        for size in range(min(len(merged), len(nxt), 2000), 19, -1):
            if merged.endswith(nxt[:size]):
                overlap = size
                break
        merged += ("" if overlap else "\n\n") + nxt[overlap:]
    return merged
