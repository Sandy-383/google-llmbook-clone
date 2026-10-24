import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable

import docx
import pypdfium2 as pdfium
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingestion.document import Block, ParsedDocument, ParseError
from app.ingestion.ocr import Ocr

# (pages done, total pages); lets the pipeline report progress on long PDFs.
PageProgress = Callable[[int, int], None]

# A page with less text than this is probably a scanned image.
_MIN_TEXT_CHARS = 25
# Guards against ZIP bombs: a small .docx that inflates to gigabytes.
_MAX_DOCX_UNPACKED_BYTES = 300 * 1024 * 1024


def parse_document(
    kind: str,
    data: bytes,
    *,
    max_pdf_pages: int,
    max_text_chars: int,
    ocr: Ocr | None,
    on_page: PageProgress | None = None,
) -> ParsedDocument:
    if kind == "pdf":
        return parse_pdf(data, max_pages=max_pdf_pages, ocr=ocr, on_page=on_page)
    if kind == "docx":
        return parse_docx(data)
    if kind in ("text", "markdown"):
        return parse_text(data, markdown=kind == "markdown", max_chars=max_text_chars)
    raise ParseError(f"Unsupported source type: {kind}")


# --- PDF ---------------------------------------------------------------------------------------


def parse_pdf(
    data: bytes, *, max_pages: int, ocr: Ocr | None, on_page: PageProgress | None = None
) -> ParsedDocument:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise ParseError(
                "This PDF is password-protected. Remove the password and upload it again."
            ) from exc
        raise ParseError("This file couldn't be read as a PDF. It may be damaged.") from exc

    pages: list[str] = []
    ocr_pages = 0
    try:
        total = len(pdf)
        if total == 0:
            raise ParseError("This PDF has no pages.")
        if total > max_pages:
            raise ParseError(f"This PDF has {total} pages; the limit is {max_pages}.")
        for index in range(total):
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
                if ocr is not None and len(text.strip()) < _MIN_TEXT_CHARS:
                    text = ocr.page_text(page)
                    ocr_pages += 1
            finally:
                page.close()
            pages.append(text)
            if on_page:
                on_page(index + 1, total)
    finally:
        pdf.close()

    blocks = [
        Block(text=paragraph, page=number)
        for number, text in enumerate(remove_repeated_lines(pages), start=1)
        for paragraph in split_paragraphs(text)
    ]
    return ParsedDocument(blocks=blocks, page_count=len(pages), ocr_page_count=ocr_pages)


def remove_repeated_lines(pages: list[str]) -> list[str]:
    """Drop running headers and footers: short lines at the top or bottom of pages that recur
    (ignoring digits, so "Page 3 of 40" matches "Page 4 of 40") on at least half the pages.
    They add noise to every chunk and confuse retrieval."""
    if len(pages) < 4:
        return pages

    def key(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip().lower())

    def edge_indexes(lines: list[str]) -> set[int]:
        nonblank = [i for i, line in enumerate(lines) if line.strip()]
        return set(nonblank[:2] + nonblank[-2:])

    split_pages = [text.splitlines() for text in pages]
    counts: Counter[str] = Counter()
    for lines in split_pages:
        counts.update({key(lines[i]) for i in edge_indexes(lines) if len(lines[i].strip()) <= 100})
    repeated = {k for k, n in counts.items() if n >= len(pages) / 2}
    if not repeated:
        return pages

    return [
        "\n".join(
            line for i, line in enumerate(lines) if not (i in edges and key(line) in repeated)
        )
        for lines, edges in ((lines, edge_indexes(lines)) for lines in split_pages)
    ]


def split_paragraphs(text: str) -> list[str]:
    """Rejoin PDF lines into paragraphs: blank lines separate paragraphs, words hyphenated
    across a line break are rejoined, and single line breaks become spaces."""
    # PDFium reports a hyphen at a line break as U+FFFE; restore it so "English-to-German"
    # doesn't become one unsearchable token.
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").replace("￾", "-")
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)
    paragraphs = []
    for raw in re.split(r"\n\s*\n", text):
        paragraph = re.sub(r"\s+", " ", raw).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


# --- Word --------------------------------------------------------------------------------------


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ParseError("This file isn't a Word document.")
            if sum(info.file_size for info in archive.infolist()) > _MAX_DOCX_UNPACKED_BYTES:
                raise ParseError("This document is too large once unpacked.")
        document = docx.Document(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ParseError("This Word document couldn't be read. It may be damaged.") from exc

    blocks: list[Block] = []
    headings = _HeadingPath()
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue
            level = _heading_level(item.style.name if item.style is not None else None)
            if level is None:
                blocks.append(Block(text=text, heading_path=headings.path))
            else:
                headings.enter(level, text)
                blocks.append(Block(text=text, heading_path=headings.path, is_heading=True))
        elif isinstance(item, Table):
            rows = []
            for row in item.rows:
                # Merged cells repeat their text in every spanned cell; keep one copy.
                cells: list[str] = []
                for cell in row.cells:
                    value = cell.text.strip()
                    if not cells or cells[-1] != value:
                        cells.append(value)
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append(Block(text="\n".join(rows), heading_path=headings.path))
    return ParsedDocument(blocks=blocks)


def _heading_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    if style_name == "Title":
        return 0  # sits above "Heading 1"
    match = re.fullmatch(r"Heading (\d)", style_name)
    return int(match.group(1)) if match else None


class _HeadingPath:
    """Tracks the chain of headings above the current paragraph. Entering a heading drops
    any deeper ones; levels may be skipped (a Heading 3 straight under a Heading 1)."""

    def __init__(self) -> None:
        self._levels: dict[int, str] = {}

    def enter(self, level: int, title: str) -> None:
        self._levels = {k: v for k, v in self._levels.items() if k < level}
        self._levels[level] = title

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(self._levels[k] for k in sorted(self._levels))


# --- Plain text & Markdown ---------------------------------------------------------------------


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Legacy Windows files; cp1252 decodes almost anything, unknown bytes become U+FFFD.
        return data.decode("cp1252", errors="replace")


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def parse_text(data: bytes, *, markdown: bool, max_chars: int) -> ParsedDocument:
    text = decode_text(data).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > max_chars:
        raise ParseError(f"This text is too long ({len(text):,} characters; limit {max_chars:,}).")

    blocks: list[Block] = []
    headings = _HeadingPath()
    for raw in re.split(r"\n\s*\n", text):
        lines: list[str] = []
        for line in raw.split("\n"):
            heading = _MD_HEADING.match(line) if markdown else None
            if heading is None:
                lines.append(line)
                continue
            if lines:
                blocks.append(Block(text="\n".join(lines).strip(), heading_path=headings.path))
                lines = []
            headings.enter(len(heading.group(1)), heading.group(2))
            blocks.append(Block(text=heading.group(2), heading_path=headings.path, is_heading=True))
        paragraph = "\n".join(lines).strip()
        if paragraph:
            blocks.append(Block(text=paragraph, heading_path=headings.path))
    return ParsedDocument(blocks=blocks)
