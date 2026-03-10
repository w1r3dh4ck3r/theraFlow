"""End-to-end simulation: full WhatsApp conversation → Sheets write → Telegram alert.

Simulates real webhook POST calls through the FastAPI app with mocked
outbound HTTP (WhatsApp API + Telegram API).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from theraflow.config import settings
from theraflow.conversation.engine import ConversationEngine
from theraflow.main import app
from theraflow.notifications.telegram import TelegramNotifier
from theraflow.sheets.client import SheetsClient


def _sign_payload(body: bytes) -> str:
    digest = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _whatsapp_payload(
    sender: str, *, text: str | None = None, button_id: str | None = None, button_title: str | None = None,
) -> dict[str, Any]:
    if text:
        message: dict[str, Any] = {"id": "wamid_test", "from": sender, "type": "text", "text": {"body": text}}
    elif button_id:
        message = {
            "id": "wamid_test", "from": sender, "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": button_title or ""}},
        }
    else:
        raise ValueError("Must provide text or button_id")

    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "entry1", "changes": [{"value": {
            "messaging_product": "whatsapp",
            "contacts": [{"wa_id": sender, "profile": {"name": "Test User"}}],
            "messages": [message],
        }, "field": "messages"}]}],
    }


class WebhookClient:
    def __init__(self, client: AsyncClient):
        self._client = client

    async def send_text(self, sender: str, text: str) -> httpx.Response:
        payload = _whatsapp_payload(sender, text=text)
        body = json.dumps(payload).encode()
        return await self._client.post(
            "/webhook/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign_payload(body)},
        )

    async def send_button(self, sender: str, button_id: str, title: str = "") -> httpx.Response:
        payload = _whatsapp_payload(sender, button_id=button_id, button_title=title)
        body = json.dumps(payload).encode()
        return await self._client.post(
            "/webhook/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign_payload(body)},
        )


# Full happy path answers.
# The FIRST message to a new contact always creates a session and returns the
# GREETING prompt — the message content is NOT processed as an answer.  So the
# flow is: trigger → answer GREETING → answer WHO_FOR → … → answer CONSENT.
HAPPY_PATH_STEPS = [
    ("text", "oi"),                                      # Trigger → session created, GREETING prompt
    ("button", "opt_0", "Sim"),                          # GREETING → Sim
    ("text", "1"),                                       # WHO_FOR → Para mim
    ("button", "opt_0", "Mulher"),                       # GENDER → Mulher
    ("text", "3"),                                       # AGE_GROUP → 18–24
    ("text", "São Paulo"),                               # CITY
    ("button", "opt_0", "Online"),                       # FORMAT → Online
    ("button", "opt_0", "Sim"),                          # FIRST_THERAPY → Sim
    ("text", "1"),                                       # TOPIC → Ansiedade
    ("text", "1"),                                       # URGENCY → O quanto antes
    ("text", "4"),                                       # PREFERRED_TIME → Flexível
    ("button", "opt_0", "Sim"),                          # APPOINTMENT_INTENT → Sim
    ("text", "Estou passando por um momento difícil"),   # OPTIONAL_NOTE
    ("button", "opt_0", "Sim"),                          # CONSENT → Sim → CLOSING
]


@pytest.fixture
async def sim():
    """Set up app with mocked HTTP (WhatsApp + Telegram), return (client, outbound_calls)."""
    outbound: list[dict] = []

    mock_http = AsyncMock(spec=httpx.AsyncClient)

    async def capture_post(url, **kwargs):
        outbound.append({"url": str(url), **kwargs})
        # Build a proper response with a request object so raise_for_status() works
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"messages": [{"id": "ok"}], "ok": True, "result": True}, request=request)

    mock_http.post = AsyncMock(side_effect=capture_post)

    # Inject mocked dependencies directly into app state
    telegram = TelegramNotifier(http_client=mock_http)
    engine = ConversationEngine(sheets_client=None, telegram_notifier=telegram)
    app.state.http_client = mock_http
    app.state.engine = engine

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, outbound

    # Cleanup
    del app.state.engine
    del app.state.http_client


@pytest.fixture
async def sim_with_sheets():
    """Set up app with real Sheets client + mocked HTTP, return (client, outbound, sheets_client)."""
    outbound: list[dict] = []

    mock_http = AsyncMock(spec=httpx.AsyncClient)

    async def capture_post(url, **kwargs):
        outbound.append({"url": str(url), **kwargs})
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"messages": [{"id": "ok"}], "ok": True, "result": True}, request=request)

    mock_http.post = AsyncMock(side_effect=capture_post)

    sheets = SheetsClient(
        service_account_json=settings.google_service_account_json,
        sheet_id=settings.google_sheets_id,
    )
    telegram = TelegramNotifier(http_client=mock_http)
    engine = ConversationEngine(sheets_client=sheets, telegram_notifier=telegram)
    app.state.http_client = mock_http
    app.state.engine = engine

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, outbound, sheets

    del app.state.engine
    del app.state.http_client


# ── Basic endpoint tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_endpoint(sim):
    client, _ = sim
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_verification(sim):
    client, _ = sim
    resp = await client.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.verify_token": settings.whatsapp_verify_token,
        "hub.challenge": "test_challenge_123",
    })
    assert resp.status_code == 200
    assert resp.text == "test_challenge_123"


@pytest.mark.asyncio
async def test_webhook_verification_bad_token(sim):
    client, _ = sim
    resp = await client.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "wrong_token",
        "hub.challenge": "x",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_signature_invalid(sim):
    client, _ = sim
    body = json.dumps({"object": "test"}).encode()
    resp = await client.post(
        "/webhook/whatsapp", content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=invalid"},
    )
    assert resp.status_code == 403


# ── Full flow simulation tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_happy_path(sim):
    """Complete 14-step conversation → WhatsApp replies + Telegram notification."""
    client, outbound = sim
    wh = WebhookClient(client)
    sender = "5511999990001"

    for step_type, value, *extra in HAPPY_PATH_STEPS:
        if step_type == "button":
            resp = await wh.send_button(sender, value, extra[0] if extra else "")
        else:
            resp = await wh.send_text(sender, value)
        assert resp.status_code == 200, f"Step failed at {step_type}={value}: {resp.status_code}"

    whatsapp_calls = [c for c in outbound if "graph.facebook.com" in c["url"]]
    telegram_calls = [c for c in outbound if "telegram" in c["url"]]

    assert len(whatsapp_calls) >= 14, f"Expected >=14 WhatsApp calls, got {len(whatsapp_calls)}"
    assert len(telegram_calls) == 1, f"Expected 1 Telegram call, got {len(telegram_calls)}"

    tg_payload = telegram_calls[0].get("json", {})
    tg_text = tg_payload.get("text", "")
    assert "Novo lead" in tg_text
    assert "Ansiedade" in tg_text
    assert tg_payload.get("parse_mode") == "HTML"

    print(f"\n  WhatsApp messages sent: {len(whatsapp_calls)}")
    print(f"  Telegram notifications: {len(telegram_calls)}")
    print(f"  Telegram message:\n  {tg_text}")


@pytest.mark.asyncio
async def test_human_handoff(sim):
    """'Falar com alguém' at greeting → handoff, no Telegram."""
    client, outbound = sim
    wh = WebhookClient(client)
    sender = "5511999990002"

    # Trigger session creation → GREETING prompt
    resp = await wh.send_text(sender, "oi")
    assert resp.status_code == 200

    # Answer GREETING with handoff option
    resp = await wh.send_button(sender, "opt_1", "Falar com alguém")
    assert resp.status_code == 200

    whatsapp_calls = [c for c in outbound if "graph.facebook.com" in c["url"]]
    telegram_calls = [c for c in outbound if "telegram" in c["url"]]

    assert len(whatsapp_calls) == 2, "Should send GREETING prompt + handoff message"
    assert len(telegram_calls) == 0, "No Telegram on handoff"


@pytest.mark.asyncio
async def test_lgpd_consent_denied(sim):
    """Full flow but declines consent → no Telegram."""
    client, outbound = sim
    wh = WebhookClient(client)
    sender = "5511999990003"

    # Run all steps except the last (CONSENT answer)
    for step_type, value, *extra in HAPPY_PATH_STEPS[:-1]:
        if step_type == "button":
            await wh.send_button(sender, value, extra[0] if extra else "")
        else:
            await wh.send_text(sender, value)

    # Decline consent
    resp = await wh.send_button(sender, "opt_1", "Não")
    assert resp.status_code == 200

    telegram_calls = [c for c in outbound if "telegram" in c["url"]]
    assert len(telegram_calls) == 0


@pytest.mark.asyncio
async def test_concurrent_users(sim):
    """Two users interleaved — independent sessions."""
    client, _ = sim
    wh = WebhookClient(client)

    # Trigger session creation for both users
    assert (await wh.send_text("5511000000001", "oi")).status_code == 200
    assert (await wh.send_text("5511000000002", "oi")).status_code == 200
    # Both answer GREETING
    assert (await wh.send_button("5511000000001", "opt_0", "Sim")).status_code == 200
    assert (await wh.send_button("5511000000002", "opt_0", "Sim")).status_code == 200
    # Both answer WHO_FOR (interleaved)
    assert (await wh.send_text("5511000000001", "1")).status_code == 200
    assert (await wh.send_text("5511000000002", "2")).status_code == 200
    # Both answer GENDER
    assert (await wh.send_button("5511000000001", "opt_0", "Mulher")).status_code == 200
    assert (await wh.send_button("5511000000002", "opt_1", "Homem")).status_code == 200


@pytest.mark.asyncio
async def test_invalid_input_reprompt(sim):
    """Invalid button → reprompt; valid → advances."""
    client, outbound = sim
    wh = WebhookClient(client)
    sender = "5511999990004"

    # Trigger session → GREETING prompt (1 call)
    assert (await wh.send_text(sender, "oi")).status_code == 200
    # Invalid button at GREETING → error text + GREETING reprompt (2 calls)
    assert (await wh.send_button(sender, "opt_99", "bogus")).status_code == 200
    # Valid button at GREETING → advance to WHO_FOR (1 call)
    assert (await wh.send_button(sender, "opt_0", "Sim")).status_code == 200
    # Answer WHO_FOR → advance to GENDER (1 call)
    assert (await wh.send_text(sender, "1")).status_code == 200

    whatsapp_calls = [c for c in outbound if "graph.facebook.com" in c["url"]]
    assert len(whatsapp_calls) >= 5


@pytest.mark.asyncio
async def test_full_happy_path_with_sheets(sim_with_sheets):
    """Complete flow → verify row appears in Google Sheets."""
    client, outbound, sheets = sim_with_sheets
    wh = WebhookClient(client)
    sender = "5511999990099"

    for step_type, value, *extra in HAPPY_PATH_STEPS:
        if step_type == "button":
            resp = await wh.send_button(sender, value, extra[0] if extra else "")
        else:
            resp = await wh.send_text(sender, value)
        assert resp.status_code == 200, f"Step failed at {step_type}={value}: {resp.status_code}"

    # Verify Sheets row was written
    import asyncio
    rows = await asyncio.get_event_loop().run_in_executor(
        None, sheets._worksheet.get_all_values,
    )
    # Find our test row by phone number
    matching = [r for r in rows if sender in r]
    assert len(matching) >= 1, f"Expected lead row for {sender} in Sheets, found none"
    row = matching[-1]
    print(f"\n  Sheets row: {row}")

    # Verify key fields in the row
    assert "Para mim" in row        # who_for
    assert "Mulher" in row           # gender
    assert "São Paulo" in row        # city
    assert "Ansiedade" in row        # topic
    assert "Sim" in row              # consent

    # Clean up: delete the test row
    for i, r in enumerate(rows):
        if sender in r:
            await asyncio.get_event_loop().run_in_executor(
                None, sheets._worksheet.delete_rows, i + 1,
            )
            break


@pytest.mark.asyncio
async def test_score_calculation():
    """Lead scoring: appointment(3) + urgency(2) + note(1)."""
    from theraflow.sheets.client import calculate_score

    assert calculate_score({"appointment_interest": "Sim", "urgency": "O quanto antes", "note": "x"}) == (6, "Hot")
    assert calculate_score({"appointment_interest": "Sim", "urgency": "O quanto antes"}) == (5, "Hot")
    assert calculate_score({"appointment_interest": "Sim", "urgency": "Neste mês"}) == (3, "Warm")
    assert calculate_score({}) == (0, "Low")
