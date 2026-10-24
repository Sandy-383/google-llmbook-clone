import uuid

import pytest

from app.chat.citations import normalize_citations, strip_citations
from app.retrieval.search import (
    _ChunkRow,
    build_passages,
    merge_overlapping,
    or_tsquery,
    reciprocal_rank_fusion,
)


@pytest.mark.parametrize(
    ("answer", "count", "expected", "cited"),
    [
        ("Leave is 27 days [1].", 2, "Leave is 27 days [1].", [1]),
        ("Both say so [1, 2].", 2, "Both say so [1][2].", [1, 2]),
        ("Made up source [7].", 2, "Made up source.", []),
        ("Mixed [2, 9] cite.", 2, "Mixed [2] cite.", [2]),
        ("Published in [2017] and cited [1].", 2, "Published in [2017] and cited [1].", [1]),
        ("No citations here.", 0, "No citations here.", []),
    ],
)
def test_normalize_citations(answer: str, count: int, expected: str, cited: list[int]) -> None:
    assert normalize_citations(answer, count) == (expected, cited)


def test_strip_citations() -> None:
    assert strip_citations("It is 27 days [1][2]. Also [3, 4] this.") == "It is 27 days. Also this."


def test_rrf_rewards_agreement_between_rankings() -> None:
    # 3 is only second in both lists, but beats items that rank first in just one.
    assert reciprocal_rank_fusion([[1, 3, 5], [2, 3, 6]])[0] == 3


def test_or_tsquery_quotes_and_drops_unsafe_lexemes() -> None:
    assert or_tsquery(["leav", "annual", "it's", "a:b", "annual"]) == "'annual' | 'leav'"
    assert or_tsquery([]) == ""


def test_merge_overlapping_drops_the_repeated_boundary() -> None:
    first = "Alpha sentence one. The shared tail sentence is here."
    second = "The shared tail sentence is here. Beta continues."
    assert merge_overlapping([first, second]) == (
        "Alpha sentence one. The shared tail sentence is here. Beta continues."
    )
    assert merge_overlapping(["No overlap.", "Next part."]) == "No overlap.\n\nNext part."


def row(source: uuid.UUID, ord_: int, text: str, page: int | None = None) -> _ChunkRow:
    return _ChunkRow(ord_ + 1000 * (source.int % 7), source, ord_, text, None, page, page)


def test_passages_add_neighbours_merge_runs_and_number_by_rank() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    hit_a = row(a, 5, "A5 hit", page=3)
    hit_b = row(b, 0, "B0 hit")
    neighbours = {(a, 4): row(a, 4, "A4 before", page=3), (a, 6): row(a, 6, "A6 after", page=4)}
    passages = build_passages(
        [hit_b, hit_a],
        neighbours,
        {a: "Doc A", b: "Doc B"},
        {hit_a.id: 0.8, hit_b.id: 0.6},
        top_k=6,
        token_budget=10_000,
    )
    assert [(p.number, p.source_title) for p in passages] == [(1, "Doc B"), (2, "Doc A")]
    doc_a = passages[1]
    assert doc_a.text == "A4 before\n\nA5 hit\n\nA6 after"
    assert (doc_a.page_start, doc_a.page_end) == (3, 4)
    assert doc_a.label() == "Doc A, pp. 3-4"
    assert doc_a.similarity == 0.8


def test_long_runs_are_split_into_separate_passages() -> None:
    s = uuid.uuid4()
    hits = [row(s, i, f"chunk {i}", page=i + 1) for i in range(5)]
    passages = build_passages(hits, {}, {s: "S"}, {}, top_k=6, token_budget=10_000)
    assert [p.chunk_ids for p in passages] == [[h.id for h in hits[:3]], [h.id for h in hits[3:]]]
    assert [(p.page_start, p.page_end) for p in passages] == [(1, 3), (4, 5)]


def test_passages_respect_the_token_budget() -> None:
    s = uuid.uuid4()
    hits = [row(s, i * 10, "x" * 320) for i in range(5)]  # ~100 tokens each
    passages = build_passages(hits, {}, {s: "S"}, {}, top_k=6, token_budget=250)
    assert len(passages) == 2
