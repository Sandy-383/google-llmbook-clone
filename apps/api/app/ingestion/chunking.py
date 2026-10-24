import itertools
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass

from app.ingestion.document import Block, ParsedDocument

# Split after sentence-ending punctuation (incl. Devanagari danda and CJK full stop).
_SENTENCE_END = re.compile(r"(?<=[.!?…।。])\s+")
# Sections are kept apart at the top two heading levels; deeper headings may share a chunk.
_SECTION_DEPTH = 2


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English with the Qwen/Llama/XLM-R tokenizers.
    Used only to size chunks; the chat model's real token count is checked at query time."""
    return max(1, math.ceil(len(text) / 4))


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    heading: str | None
    page_start: int | None
    page_end: int | None
    token_count: int


@dataclass(frozen=True)
class _Unit:
    text: str
    block: int  # index of the source block, to know how to join units back together
    page: int | None
    heading_path: tuple[str, ...]
    tokens: int


def chunk_document(
    document: ParsedDocument, *, target_tokens: int, overlap_tokens: int
) -> list[ChunkDraft]:
    """Pack paragraphs (or sentences of long paragraphs) into chunks of about `target_tokens`.

    Consecutive chunks share up to `overlap_tokens` of trailing text, so a fact that straddles a
    boundary is still retrievable. Chunks never cross a top-level section boundary.
    """
    chunks: list[ChunkDraft] = []
    current: list[_Unit] = []
    size = 0

    for unit in _units(document.blocks, target_tokens):
        if current:
            new_section = (
                unit.heading_path[:_SECTION_DEPTH] != current[-1].heading_path[:_SECTION_DEPTH]
            )
            if new_section or size + unit.tokens > target_tokens:
                chunks.append(_draft(current))
                carry = [] if new_section else _overlap(current, overlap_tokens)
                current, size = carry, sum(u.tokens for u in carry)
        current.append(unit)
        size += unit.tokens

    if current:
        chunks.append(_draft(current))
    return chunks


def _units(blocks: list[Block], max_tokens: int) -> Iterator[_Unit]:
    for index, block in enumerate(blocks):
        # Headings travel with their content via heading_path rather than as text of their own.
        if block.is_heading:
            continue
        pieces = (
            [block.text]
            if estimate_tokens(block.text) <= max_tokens
            else _SENTENCE_END.split(block.text)
        )
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            for part in _split_oversized(piece, max_tokens):
                yield _Unit(part, index, block.page, block.heading_path, estimate_tokens(part))


def _split_oversized(text: str, max_tokens: int) -> Iterator[str]:
    """A "sentence" with no punctuation (tables, lists, OCR output) can still be huge."""
    if estimate_tokens(text) <= max_tokens:
        yield text
        return
    max_chars = max_tokens * 4
    words = text.split()
    if len(words) <= 1:  # no spaces at all (e.g. CJK): cut by characters
        for start in range(0, len(text), max_chars):
            yield text[start : start + max_chars]
        return
    window: list[str] = []
    length = 0
    for word in words:
        if window and length + len(word) + 1 > max_chars:
            yield " ".join(window)
            window, length = [], 0
        window.append(word)
        length += len(word) + 1
    if window:
        yield " ".join(window)


def _overlap(units: list[_Unit], overlap_tokens: int) -> list[_Unit]:
    carry: list[_Unit] = []
    total = 0
    for unit in reversed(units):
        if total + unit.tokens > overlap_tokens:
            break
        carry.insert(0, unit)
        total += unit.tokens
    # Carrying everything would just repeat the previous chunk.
    return [] if len(carry) == len(units) else carry


def _draft(units: list[_Unit]) -> ChunkDraft:
    parts = [units[0].text]
    for previous, unit in itertools.pairwise(units):
        # Sentences of one paragraph rejoin with a space; separate paragraphs keep a blank line.
        parts.append((" " if unit.block == previous.block else "\n\n") + unit.text)
    text = "".join(parts)
    pages = [u.page for u in units if u.page is not None]
    heading = " › ".join(units[0].heading_path)[:500] or None
    return ChunkDraft(
        text=text,
        heading=heading,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        token_count=estimate_tokens(text),
    )
