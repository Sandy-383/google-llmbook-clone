FROM python:3.12-slim

# Tesseract reads scanned PDF pages. English is always installed; add more with e.g.
#   docker compose build --build-arg EXTRA_OCR_LANGS="hin kan"   (and set OCR_LANGUAGES=eng+hin+kan)
ARG EXTRA_OCR_LANGS=""
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
       $(for lang in $EXTRA_OCR_LANGS; do echo "tesseract-ocr-$lang"; done) \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /bin/uv

WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data/files && chown app /data/files
USER app

EXPOSE 8000
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
