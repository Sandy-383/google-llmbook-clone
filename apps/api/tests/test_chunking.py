import itertools

from app.ingestion.chunking import chunk_document, estimate_tokens
from app.ingestion.document import Block, ParsedDocument


def sentence(n: int) -> str:
    return f"Sentence number {n} talks about policy detail {n} in plain words."  # ~16 tokens


def test_empty_document_has_no_chunks() -> None:
    assert chunk_document(ParsedDocument(), target_tokens=100, overlap_tokens=20) == []


def test_chunks_stay_near_target_and_overlap() -> None:
    paragraph = " ".join(sentence(i) for i in range(60))
    chunks = chunk_document(
        ParsedDocument(blocks=[Block(paragraph, page=1)]), target_tokens=100, overlap_tokens=20
    )
    assert len(chunks) > 5
    assert all(c.token_count <= 100 + 20 for c in chunks)
    # Each chunk starts with the tail of the previous one.
    for previous, current in itertools.pairwise(chunks):
        first_sentence = current.text.split(". ")[0] + "."
        assert first_sentence in previous.text
    # Nothing is lost.
    joined = " ".join(c.text for c in chunks)
    assert all(sentence(i) in joined for i in range(60))


def test_sections_are_not_mixed_and_carry_their_heading() -> None:
    doc = ParsedDocument(
        blocks=[
            Block("Travel", heading_path=("Travel",), is_heading=True),
            Block("Economy class for short flights.", heading_path=("Travel",)),
            Block("Leave", heading_path=("Leave",), is_heading=True),
            Block("27 days of annual leave.", heading_path=("Leave",)),
        ]
    )
    chunks = chunk_document(doc, target_tokens=400, overlap_tokens=60)
    assert [(c.heading, c.text) for c in chunks] == [
        ("Travel", "Economy class for short flights."),
        ("Leave", "27 days of annual leave."),
    ]


def test_subsections_may_share_a_chunk() -> None:
    doc = ParsedDocument(
        blocks=[
            Block("A.", heading_path=("Guide", "Part 1", "Detail a")),
            Block("B.", heading_path=("Guide", "Part 1", "Detail b")),
        ]
    )
    chunks = chunk_document(doc, target_tokens=400, overlap_tokens=60)
    assert len(chunks) == 1
    assert chunks[0].text == "A.\n\nB."
    assert chunks[0].heading == "Guide › Part 1 › Detail a"


def test_page_range_spans_the_pages_a_chunk_covers() -> None:
    doc = ParsedDocument(blocks=[Block("End of page one.", page=1), Block("Top of two.", page=2)])
    (chunk,) = chunk_document(doc, target_tokens=400, overlap_tokens=60)
    assert (chunk.page_start, chunk.page_end) == (1, 2)


def test_text_without_punctuation_is_still_split() -> None:
    table_dump = " ".join(f"cell{i}" for i in range(2000))
    chunks = chunk_document(
        ParsedDocument(blocks=[Block(table_dump)]), target_tokens=100, overlap_tokens=0
    )
    assert len(chunks) > 10
    assert all(c.token_count <= 110 for c in chunks)


def test_text_without_spaces_is_split_by_characters() -> None:
    chunks = chunk_document(
        ParsedDocument(blocks=[Block("字" * 2000)]), target_tokens=100, overlap_tokens=0
    )
    assert len(chunks) == 5
    assert estimate_tokens(chunks[0].text) == 100
