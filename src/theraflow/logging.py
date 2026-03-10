"""Structured logging configuration using structlog with JSON output.

Call ``configure_logging()`` once at application startup (before any log
statements are emitted) to set up the global structlog and stdlib logging
pipelines.

Usage::

    from theraflow.logging import configure_logging, get_logger

    configure_logging()
    log = get_logger(__name__)
    log.info("server_started", host="0.0.0.0", port=8000)
"""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Configure structlog and the stdlib root logger.

    Args:
        level: Minimum log level as a string (e.g. ``"DEBUG"``, ``"INFO"``).
        json_logs: When *True* (default) emit newline-delimited JSON.
                   Set to *False* for a human-readable console format during
                   local development.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_logs:
        # Production: compact JSON, one object per line.
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(sys.stdout),
            cache_logger_on_first_use=True,
        )
    else:
        # Development: coloured key=value output.
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(sys.stdout),
            cache_logger_on_first_use=True,
        )

    # Route stdlib logging (e.g. from uvicorn / httpx) through structlog.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    logging.getLogger("uvicorn").setLevel(log_level)
    logging.getLogger("uvicorn.access").setLevel(log_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for *name*.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A structlog :class:`~structlog.stdlib.BoundLogger` instance.
    """
    return structlog.get_logger(name)
