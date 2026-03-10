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
from theraflow.config import settings
from theraflow.conversation.engine import ConversationEngine
from theraflow.logging import configure_logging, get_logger
from theraflow.notifications.telegram import TelegramNotifier
from theraflow.sheets.client import SheetsClient
from theraflow.whatsapp import router as whatsapp_router

# Recorded once when the process starts; used to compute /health uptime.
_start_time: float = time.monotonic()

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler.

    On startup:

    1. Configures structured logging.
    2. Instantiates :class:`~theraflow.sheets.client.SheetsClient` and
       :class:`~theraflow.notifications.telegram.TelegramNotifier`.
    3. Creates the :class:`~theraflow.conversation.engine.ConversationEngine`
       with references to both, and stores it on ``app.state.engine`` so the
       webhook handler can retrieve it per request.

    On shutdown:

    * Logs the number of active sessions still in memory.
    * Any further cleanup (e.g. closing persistent HTTP connections) can be
      added after the ``yield``.
    """
    configure_logging()

    sheets_client = SheetsClient(
        service_account_json=settings.google_service_account_json,
        sheet_id=settings.google_sheets_id,
    )
    telegram_notifier = TelegramNotifier()
    engine = ConversationEngine(
        sheets_client=sheets_client,
        telegram_notifier=telegram_notifier,
    )
    app.state.engine = engine

    log.info("theraflow_started", version=__version__)

    yield

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------
    active_sessions = len(engine._sessions)  # noqa: SLF001
    log.info(
        "theraflow_stopped",
        active_sessions_discarded=active_sessions,
    )


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
