import re

# [1], [12], [1, 2] — at most three digits, so a bracketed year like [2017] isn't a citation.
_CITATION = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


def _tidy(text: str) -> str:
    """Removing a marker can leave "fact ." or a double space behind."""
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def normalize_citations(answer: str, passage_count: int) -> tuple[str, list[int]]:
    """Rewrite "[1, 2]" as "[1][2]" and drop numbers that don't match a passage (models
    occasionally invent one). Returns the cleaned text and the passages actually cited."""
    cited: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        valid = [n for n in map(int, re.findall(r"\d+", match.group(1))) if 1 <= n <= passage_count]
        cited.update(valid)
        return "".join(f"[{n}]" for n in valid)

    return _tidy(_CITATION.sub(replace, answer)), sorted(cited)


def strip_citations(text: str) -> str:
    return _tidy(_CITATION.sub("", text))
