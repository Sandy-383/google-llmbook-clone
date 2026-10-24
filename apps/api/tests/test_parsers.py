import io
import zipfile

import pytest

from app.ingestion.document import ParseError
from app.ingestion.ocr import Ocr
from app.ingestion.parsers import (
    parse_docx,
    parse_pdf,
    parse_text,
    remove_repeated_lines,
    split_paragraphs,
)
from app.ingestion.sniff import UnsupportedFileError, detect_kind
from tests.factories import HANDBOOK_PAGES, make_docx, make_pdf, make_scanned_pdf

# --- type detection ---


@pytest.mark.parametrize(
    ("head", "filename", "kind"),
    [
        (b"%PDF-1.7\n...", "anything.bin", "pdf"),
        (b"\xef\xbb\xbfjunk%PDF-1.4", "x.pdf", "pdf"),
        (b"PK\x03\x04....", "report.docx", "docx"),
        (b"Plain notes", "notes.txt", "text"),
        (b"# Title", "README.md", "markdown"),
        (b"\xff\xfeh\x00i\x00", "utf16.txt", "text"),
    ],
)
def test_detect_kind(head: bytes, filename: str, kind: str) -> None:
    assert detect_kind(head, filename) == kind


@pytest.mark.parametrize(
    ("head", "filename"),
    [
        (b"\x89PNG\r\n\x1a\n", "image.png"),
        (b"PK\x03\x04....", "sheet.xlsx"),
        (b"MZ\x90\x00", "notes.txt"),  # binary pretending to be text
        (b"hello", "script.html"),
    ],
)
def test_detect_kind_rejects(head: bytes, filename: str) -> None:
    with pytest.raises(UnsupportedFileError):
        detect_kind(head, filename)


# --- PDF ---


def test_pdf_text_and_pages() -> None:
    doc = parse_pdf(make_pdf(HANDBOOK_PAGES), max_pages=100, ocr=None)
    assert doc.page_count == 4
    assert doc.ocr_page_count == 0
    leave = [b for b in doc.blocks if "27 days" in b.text]
    assert len(leave) == 1
    assert leave[0].page == 2


def test_pdf_running_headers_and_page_numbers_are_removed() -> None:
    doc = parse_pdf(make_pdf(HANDBOOK_PAGES), max_pages=100, ocr=None)
    text = " ".join(b.text for b in doc.blocks)
    assert "Halvorsen Instruments Handbook" not in text
    assert "Page 3" not in text
    assert "full-disk encryption" in text


def test_repeated_line_removal_needs_enough_pages() -> None:
    pages = ["Header\nBody one", "Header\nBody two"]
    assert remove_repeated_lines(pages) == pages


def test_split_paragraphs_rejoins_lines_and_hyphenation() -> None:
    text = "The infor-\nmation is\r\nhere.\r\n\r\nSecond para-\ngraph."
    assert split_paragraphs(text) == ["The information is here.", "Second paragraph."]


def test_pdfium_line_break_hyphen_is_restored() -> None:
    assert split_paragraphs("English￾to-German") == ["English-to-German"]


def test_page_limit() -> None:
    with pytest.raises(ParseError, match="limit is 2"):
        parse_pdf(make_pdf([["a"], ["b"], ["c"]]), max_pages=2, ocr=None)


def test_damaged_pdf() -> None:
    with pytest.raises(ParseError, match="couldn't be read"):
        parse_pdf(b"%PDF-1.4 this is not really a pdf", max_pages=10, ocr=None)


def test_progress_is_reported_per_page() -> None:
    seen: list[tuple[int, int]] = []
    parse_pdf(
        make_pdf(HANDBOOK_PAGES), max_pages=10, ocr=None, on_page=lambda d, t: seen.append((d, t))
    )
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]


@pytest.mark.skipif(not Ocr.available(), reason="Tesseract is not installed")
def test_scanned_page_is_read_with_ocr() -> None:
    pdf = make_scanned_pdf("Annual leave is\n27 days per year.")
    doc = parse_pdf(pdf, max_pages=10, ocr=Ocr("eng"))
    assert doc.ocr_page_count == 1
    text = " ".join(b.text for b in doc.blocks).lower()
    assert "annual leave" in text
    assert "27 days" in text


def test_scanned_page_without_ocr_yields_no_text() -> None:
    doc = parse_pdf(make_scanned_pdf("Hello"), max_pages=10, ocr=None)
    assert doc.blocks == []
    assert doc.ocr_page_count == 0


# --- Word ---


def test_docx_headings_paragraphs_and_tables() -> None:
    doc = parse_docx(make_docx())
    body = [(b.heading_path, b.text) for b in doc.blocks if not b.is_heading]
    assert body == [
        (("Employee Handbook", "Travel"), "Economy class is required for flights under 6 hours."),
        (
            ("Employee Handbook", "Leave"),
            "Full-time employees receive 27 days of paid annual leave.",
        ),
        (("Employee Handbook", "Leave"), "Leave type | Days\nParental | 112"),
    ]


def test_zip_that_is_not_a_word_document() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(ParseError, match="isn't a Word document"):
        parse_docx(buffer.getvalue())


# --- Text & Markdown ---


def test_markdown_headings_become_paths() -> None:
    md = b"# Guide\n\nIntro text.\n\n## Setup\nInstall it.\n\n## Usage\n\nRun it."
    doc = parse_text(md, markdown=True, max_chars=10_000)
    body = [(b.heading_path, b.text) for b in doc.blocks if not b.is_heading]
    assert body == [
        (("Guide",), "Intro text."),
        (("Guide", "Setup"), "Install it."),
        (("Guide", "Usage"), "Run it."),
    ]


def test_plain_text_keeps_hash_lines_as_text() -> None:
    doc = parse_text(b"# not a heading here", markdown=False, max_chars=100)
    assert [b.text for b in doc.blocks] == ["# not a heading here"]


@pytest.mark.parametrize(
    "data",
    ["Café notes".encode(), "Café notes".encode("cp1252"), "Café notes".encode("utf-16")],
)
def test_text_encodings(data: bytes) -> None:
    doc = parse_text(data, markdown=False, max_chars=100)
    assert doc.blocks[0].text == "Café notes"


def test_text_length_limit() -> None:
    with pytest.raises(ParseError, match="too long"):
        parse_text(b"x" * 101, markdown=False, max_chars=100)
