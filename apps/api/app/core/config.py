from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (and `.env` when present)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    # 127.0.0.1 rather than localhost: on Windows, localhost tries IPv6 first and stalls.
    database_url: str = "postgresql+asyncpg://llmbook:llmbook@127.0.0.1:5432/llmbook"
    db_pool_size: int = 10
    # Tests create a fresh event loop per test; pooled connections can't cross loops.
    db_null_pool: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"

    ollama_base_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen3:8b"
    embed_model: str = "bge-m3"

    session_cookie_name: str = "llmbook_session"
    session_ttl_hours: int = 24 * 14
    cookie_secure: bool = False
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]
    allow_registration: bool = True
    # When set, creating an account needs this code: share it with the people you invite.
    # Useful when the app is reachable from the internet (e.g. through a Cloudflare tunnel).
    registration_code: str | None = None

    # Per account, and a looser per-IP cap (many users can share one office/NAT address).
    login_rate_limit: int = 10
    login_ip_rate_limit: int = 50
    login_rate_window_seconds: int = 300
    register_rate_limit: int = 10
    register_rate_window_seconds: int = 3600

    max_notebooks_per_user: int = 100

    # --- Sources & ingestion ---
    storage_dir: Path = Path("data/files")
    max_upload_mb: int = 50
    max_sources_per_notebook: int = 50
    max_pdf_pages: int = 1000
    max_text_chars: int = 2_000_000
    upload_rate_limit: int = 100
    upload_rate_window_seconds: int = 3600
    ocr_enabled: bool = True
    # Tesseract language packs, e.g. "eng+hin+kan"; each must be installed in the worker image.
    ocr_languages: str = "eng"
    chunk_target_tokens: int = 400
    chunk_overlap_tokens: int = 60
    embed_batch_size: int = 16
    # Kept short so the GPU goes back to the chat model soon after a document is indexed.
    embed_keep_alive: str = "1m"

    # --- Chat ---
    chat_context_tokens: int = 8192
    chat_max_answer_tokens: int = 1024
    chat_temperature: float = 0.2
    chat_keep_alive: str = "30m"
    # Earlier exchanges sent along with a question, so follow-ups make sense.
    chat_history_turns: int = 3
    chat_max_question_chars: int = 4000
    chat_rate_limit: int = 30
    chat_rate_window_seconds: int = 600
    # Query embeddings run on the CPU: on an 8 GB GPU the chat model and bge-m3 don't fit together.
    query_embed_on_cpu: bool = True
    query_embed_keep_alive: str = "10m"

    # --- Background generation: source guides and studio outputs ---
    # A guide condenses windows of consecutive passages ("map"), then combines the notes.
    guide_window_tokens: int = 4500
    # Long documents are sampled evenly to this many windows, bounding the time per guide.
    guide_max_windows: int = 16
    studio_answer_tokens: int = 1500
    studio_rate_limit: int = 20
    studio_rate_window_seconds: int = 3600
    max_notes_per_notebook: int = 200
    max_note_chars: int = 50_000

    # --- Retrieval ---
    retrieval_candidates: int = 30  # per method (vector, keyword) before fusion
    retrieval_top_k: int = 6  # passages kept after fusion, before adding neighbours
    # Below this best cosine similarity the sources are judged not to cover the question, and the
    # model isn't called at all. Calibrated with evals/ (see README).
    retrieval_min_similarity: float = 0.35

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _production_requires_https_cookies(self) -> "Settings":
        if self.is_production and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
