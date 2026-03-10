"""FastAPI application entry point for TheraFlow.

Start the server with::

    uvicorn theraflow.main:app --host 0.0.0.0 --port 8000

Or via Docker (see Dockerfile / docker-compose.yml).
"""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from theraflow import __version__
from theraflow.logging import configure_logging, get_logger
from theraflow.whatsapp import router as whatsapp_router

# Recorded once when the process starts; used to compute /health uptime.
_start_time: float = time.monotonic()

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Application lifespan handler.

    Runs ``configure_logging()`` before the server begins accepting requests
    and emits a startup log line.  Any teardown logic (e.g. closing HTTP
    client sessions) should be added after the ``yield``.
    """
    configure_logging()
    log.info("theraflow_started", version=__version__)
    yield
    log.info("theraflow_stopped")


app = FastAPI(
    title="TheraFlow",
    version=__version__,
    description="WhatsApp lead-qualification bot for therapy practices.",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(whatsapp_router)

# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"])
async def health() -> dict[str, object]:
    """Return service liveness and uptime in seconds.

    Returns:
        A JSON object with ``status`` (always ``"ok"``) and ``uptime``
        (floating-point seconds since the process started).
    """
    return {
        "status": "ok",
        "uptime": round(time.monotonic() - _start_time, 3),
    }
