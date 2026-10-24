from dataclasses import dataclass, field


class ParseError(Exception):
    """The file itself can't be processed. Permanent; the message is shown to the user."""


@dataclass(frozen=True)
class Block:
    """A paragraph, table or heading in reading order."""

    text: str
    page: int | None = None
    heading_path: tuple[str, ...] = ()
    is_heading: bool = False


@dataclass
class ParsedDocument:
    blocks: list[Block] = field(default_factory=list)
    page_count: int | None = None
    ocr_page_count: int = 0
