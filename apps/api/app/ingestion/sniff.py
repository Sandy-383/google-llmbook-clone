from pathlib import PurePath

TEXT_EXTENSIONS = {".txt": "text", ".text": "text", ".md": "markdown", ".markdown": "markdown"}
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class UnsupportedFileError(Exception):
    """The message is safe to show to the user."""


def detect_kind(head: bytes, filename: str | None) -> str:
    """Decide the source kind from the file's bytes; the extension only picks between formats
    that share a container (ZIP) or have no signature (plain text). The browser's
    Content-Type is never trusted."""
    ext = PurePath(filename or "").suffix.lower()

    # Some generators put junk before the header; PDF readers accept it within the first 1 KB.
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        if ext == ".docx":
            return "docx"  # the parser verifies it really is a Word document
        raise UnsupportedFileError("Only Word documents (.docx) are supported among Office files.")
    if ext in TEXT_EXTENSIONS and _looks_like_text(head):
        return TEXT_EXTENSIONS[ext]
    raise UnsupportedFileError(
        "Unsupported file type. Upload a PDF, a Word document (.docx), or a .txt or .md file."
    )


def _looks_like_text(head: bytes) -> bool:
    if head.startswith(_UTF16_BOMS):
        return True
    return b"\x00" not in head
