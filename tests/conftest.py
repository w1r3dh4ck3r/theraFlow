"""Shared pytest fixtures for the TheraFlow test suite.

Environment variables must be set *before* any theraflow module is imported,
because ``theraflow.config.settings`` is a module-level singleton that reads
from the environment at import time.
"""

import os

# ---------------------------------------------------------------------------
# Inject required env vars before any theraflow import
# ---------------------------------------------------------------------------
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123456789")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test_access_token")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test_verify_token")
os.environ.setdefault("WHATSAPP_APP_SECRET", "test_app_secret")
os.environ.setdefault("GOOGLE_SHEETS_ID", "test_sheet_id")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "/tmp/fake_sa.json")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test_token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1001234567890")

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock

from theraflow.conversation.engine import ConversationEngine
from theraflow.whatsapp import router as whatsapp_router


# ---------------------------------------------------------------------------
# Downstream dependency mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sheets() -> AsyncMock:
    """Async mock for SheetsClient — records calls to write_lead."""
    client: AsyncMock = AsyncMock()
    client.write_lead = AsyncMock()
    return client


@pytest.fixture
def mock_telegram() -> AsyncMock:
    """Async mock for TelegramNotifier — records calls to send_lead_notification."""
    notifier: AsyncMock = AsyncMock()
    notifier.send_lead_notification = AsyncMock()
    return notifier


# ---------------------------------------------------------------------------
# Engine and ASGI fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(mock_sheets: AsyncMock, mock_telegram: AsyncMock) -> ConversationEngine:
    """Fresh ConversationEngine backed by mocked Sheets + Telegram clients."""
    return ConversationEngine(
        sheets_client=mock_sheets,
        telegram_notifier=mock_telegram,
    )


@pytest.fixture
def test_app(engine: ConversationEngine) -> FastAPI:
    """Minimal FastAPI app with the WhatsApp router and injected engine.

    Bypasses the production lifespan (which requires real credentials) by
    building a bare app and directly setting ``app.state.engine``.
    """
    app = FastAPI()
    app.include_router(whatsapp_router)
    app.state.engine = engine
    return app


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncClient:
    """Async httpx test client targeting the test ASGI app."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac
