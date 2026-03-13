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
    list_id: str | None = None, list_title: str | None = None,
) -> dict[str, Any]:
    if text:
        message: dict[str, Any] = {"id": "wamid_test", "from": sender, "type": "text", "text": {"body": text}}
    elif list_id:
        message = {
            "id": "wamid_test", "from": sender, "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": list_id, "title": list_title or ""}},
        }
    elif button_id:
        message = {
            "id": "wamid_test", "from": sender, "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": button_title or ""}},
        }
    else:
        raise ValueError("Must provide text, button_id, or list_id")

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

    async def send_list_reply(self, sender: str, list_id: str, title: str = "") -> httpx.Response:
        payload = _whatsapp_payload(sender, list_id=list_id, list_title=title)
        body = json.dumps(payload).encode()
        return await self._client.post(
            "/webhook/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign_payload(body)},
        )


# Full happy path using natural free-text conversation.
# GENDER keeps buttons.
HAPPY_PATH_STEPS = [
    ("text", "oi"),                                      # Trigger → GREETING
    ("text", "Maria"),                                   # GREETING (name) → WHO_FOR
    ("text", "Para mim"),                                # WHO_FOR → GENDER
    ("button", "opt_0", "Mulher"),                       # GENDER (buttons) → FIRST_THERAPY
    ("text", "Sim"),                                     # FIRST_THERAPY → TOPIC
    ("text", "Ansiedade"),                               # TOPIC → URGENCY
    ("text", "O quanto antes"),                          # URGENCY → TERMS
    ("text", "Sim"),                                     # TERMS → CLOSING
]


@pytest.fixture
async def sim():
    """Set up app with mocked HTTP (WhatsApp + Telegram), return (client, outbound_calls)."""
    outbound: list[dict] = []

    mock_http = AsyncMock(spec=httpx.AsyncClient)

    async def capture_post(url, **kwargs):
        outbound.append({"url": str(url), **kwargs})
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"messages": [{"id": "ok"}], "ok": True, "result": True}, request=request)

    mock_http.post = AsyncMock(side_effect=capture_post)

    telegram = TelegramNotifier(http_client=mock_http)
    engine = ConversationEngine(sheets_client=None, telegram_notifier=telegram)
    app.state.http_client = mock_http
    app.state.engine = engine

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, outbound

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


async def _run_steps(wh: WebhookClient, sender: str, steps: list) -> list[httpx.Response]:
    """Run a sequence of steps, returning all responses."""
    responses = []
    for step_type, value, *extra in steps:
        if step_type == "list":
            resp = await wh.send_list_reply(sender, value, extra[0] if extra else "")
        elif step_type == "button":
            resp = await wh.send_button(sender, value, extra[0] if extra else "")
        else:
            resp = await wh.send_text(sender, value)
        responses.append(resp)
    return responses


# ── Full flow simulation tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_happy_path(sim):
    """Complete conversation → WhatsApp replies + Telegram notification."""
    client, outbound = sim
    wh = WebhookClient(client)
    sender = "5511999990001"

    responses = await _run_steps(wh, sender, HAPPY_PATH_STEPS)
    for resp in responses:
        assert resp.status_code == 200

    whatsapp_calls = [c for c in outbound if "graph.facebook.com" in c["url"]]
    telegram_calls = [c for c in outbound if "telegram" in c["url"]]

    assert len(whatsapp_calls) >= 7, f"Expected >=7 WhatsApp calls, got {len(whatsapp_calls)}"
    assert len(telegram_calls) == 1, f"Expected 1 Telegram call, got {len(telegram_calls)}"

    tg_payload = telegram_calls[0].get("json", {})
    tg_text = tg_payload.get("text", "")
    assert "URGENTE" in tg_text
    assert "Ansiedade" in tg_text
    assert tg_payload.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_concurrent_users(sim):
    """Two users interleaved — independent sessions."""
    client, _ = sim
    wh = WebhookClient(client)

    assert (await wh.send_text("5511000000001", "oi")).status_code == 200
    assert (await wh.send_text("5511000000002", "oi")).status_code == 200
    assert (await wh.send_text("5511000000001", "Maria")).status_code == 200
    assert (await wh.send_text("5511000000002", "João")).status_code == 200
    assert (await wh.send_text("5511000000001", "Para mim")).status_code == 200
    assert (await wh.send_text("5511000000002", "Para mim")).status_code == 200


@pytest.mark.asyncio
async def test_handoff_keyword(sim):
    """Typing 'falar com alguém' triggers handoff at any step."""
    client, outbound = sim
    wh = WebhookClient(client)
    sender = "5511999990005"

    assert (await wh.send_text(sender, "oi")).status_code == 200
    assert (await wh.send_text(sender, "quero falar com alguém")).status_code == 200

    telegram_calls = [c for c in outbound if "telegram" in c["url"]]
    assert len(telegram_calls) == 1


@pytest.mark.asyncio
async def test_full_happy_path_with_sheets(sim_with_sheets):
    """Complete flow → verify row appears in Google Sheets."""
    client, outbound, sheets = sim_with_sheets
    wh = WebhookClient(client)
    sender = "5511999990099"

    responses = await _run_steps(wh, sender, HAPPY_PATH_STEPS)
    for resp in responses:
        assert resp.status_code == 200

    import asyncio
    rows = await asyncio.get_event_loop().run_in_executor(
        None, sheets._worksheet.get_all_values,
    )
    matching = [r for r in rows if sender in r]
    assert len(matching) >= 1, f"Expected lead row for {sender} in Sheets, found none"
    row = matching[-1]
    print(f"\n  Sheets row: {row}")

    assert "Para mim" in row
    assert "Mulher" in row
    assert "Ansiedade" in row

    # Clean up: delete the test row
    for i, r in enumerate(rows):
        if sender in r:
            await asyncio.get_event_loop().run_in_executor(
                None, sheets._worksheet.delete_rows, i + 1,
            )
            break


@pytest.mark.asyncio
async def test_urgency_declined(sim):
    """'Ainda estou pensando' at URGENCY → continues flow but scores lower."""
    client, outbound = sim
    wh = WebhookClient(client)
    sender = "5511999990006"

    # Run through to URGENCY (6 steps: trigger + name + WHO_FOR + GENDER + FIRST_THERAPY + TOPIC answer)
    await _run_steps(wh, sender, HAPPY_PATH_STEPS[:6])

    # Type "Ainda estou pensando" at URGENCY (natural step)
    resp = await wh.send_text(sender, "Ainda estou pensando")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_score_calculation():
    """Multi-axis lead quality scoring."""
    from theraflow.sheets.client import calculate_score

    # urgency only: interest (+20) + agreed (+20) = 40 → warm
    assert calculate_score({"urgency": "O quanto antes"}) == (40, "warm")
    assert calculate_score({"urgency": "Nesta semana"}) == (40, "warm")

    # urgency but not committed: interest (+20) = 20 → cold
    assert calculate_score({"urgency": "Neste mês"}) == (20, "cold")

    # no data → cold
    assert calculate_score({}) == (0, "cold")

    # full hot lead: clear topic (+20) + name+phone (+15) + interest (+20)
    #               + agreed (+20) + terms (+15) = 90 → hot
    hot_data = {
        "topic": "ansiedade e estresse no trabalho",
        "whatsapp_name": "Maria",
        "phone_number": "5511999990000",
        "urgency": "O quanto antes",
        "terms_agreement": "Sim",
    }
    score, quality = calculate_score(hot_data)
    assert quality == "hot"
    assert score == 90

    # vague responses penalty applies when >=2 values are <=2 chars
    vague_data = {"who_for": "eu", "gender": "M", "urgency": "Neste mês"}
    score_vague, _ = calculate_score(vague_data)
    score_clean, _ = calculate_score({"urgency": "Neste mês"})
    assert score_vague == score_clean - 10
