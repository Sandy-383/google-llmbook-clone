import shutil

import pypdfium2 as pdfium
import pytesseract
import structlog

log = structlog.get_logger(__name__)

_DPI = 300
# Cap the rendered size so an oversized page (posters, maps) can't exhaust memory.
_MAX_SIDE_PX = 5000
_PAGE_TIMEOUT_S = 120


class Ocr:
    """Reads text from scanned pages with Tesseract (installed in the worker image)."""

    def __init__(self, languages: str) -> None:
        self.languages = languages

    @staticmethod
    def available() -> bool:
        return shutil.which("tesseract") is not None

    def page_text(self, page: pdfium.PdfPage) -> str:
        width, height = page.get_size()  # points (1/72 inch)
        scale = min(_DPI / 72, _MAX_SIDE_PX / max(width, height, 1))
        image = page.render(scale=scale).to_pil()
        try:
            text: str = pytesseract.image_to_string(
                image, lang=self.languages, timeout=_PAGE_TIMEOUT_S
            )
        except RuntimeError:  # pytesseract raises RuntimeError on timeout
            log.warning("ocr_page_timeout")
            return ""
        return text
