import logging
import sys

import structlog
from structlog.typing import Processor


def configure_logging(level: str, *, json: bool) -> None:
    """Structured logs to stdout; JSON in containers, readable console output for local dev.

    Standard-library loggers (Celery, SQLAlchemy, ...) go through the same renderer, so every
    line in production is one JSON object.
    """
    level_no = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    # The console renderer pretty-prints exceptions itself; JSON needs them pre-formatted.
    exc_processors: list[Processor] = [structlog.processors.format_exc_info] if json else []
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            *exc_processors,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                timestamper,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *exc_processors,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level_no)
