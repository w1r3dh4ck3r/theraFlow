"""WhatsApp Cloud API message sender.

Provides async helpers for sending outbound messages via the Meta WhatsApp
Cloud API (v21.0).  All functions share a single ``httpx.AsyncClient`` per
call and log every send with structlog.

Usage::

    from theraflow.whatsapp.sender import send_text_message, send_button_message

    await send_text_message("+15551234567", "Hello!")
    await send_button_message(
        "+15551234567",
        body_text="Are you ready?",
        buttons=[{"id": "yes", "title": "Yes"}, {"id": "no", "title": "No"}],
    )
"""

from __future__ import annotations

import httpx

from theraflow.config import settings
from theraflow.logging import get_logger

log = get_logger(__name__)

_API_BASE = "https://graph.facebook.com"
_API_VERSION = "v21.0"


def _messages_url() -> str:
    """Return the Cloud API ``/messages`` endpoint URL for the configured phone number."""
    return f"{_API_BASE}/{_API_VERSION}/{settings.whatsapp_phone_number_id}/messages"


def _auth_headers() -> dict[str, str]:
    """Return the HTTP headers required for authenticated Cloud API calls."""
    return {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Public senders
# ---------------------------------------------------------------------------


async def send_text_message(phone: str, text: str) -> None:
    """Send a plain-text WhatsApp message.

    Args:
        phone: Recipient's phone number in E.164 format without the leading
            ``+`` (e.g. ``"15551234567"``), as required by the Cloud API.
        text: Message body (up to 4096 characters).

    Raises:
        httpx.HTTPStatusError: If the Cloud API returns a non-2xx response.
    """
    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text,
        },
    }

    log.info("whatsapp_send_text", phone=phone, length=len(text))

    async with httpx.AsyncClient() as client:
        response = await client.post(
            _messages_url(),
            json=payload,
            headers=_auth_headers(),
        )
        response.raise_for_status()

    log.debug("whatsapp_send_text_ok", phone=phone, status_code=response.status_code)


async def send_button_message(
    phone: str,
    body_text: str,
    buttons: list[dict],
) -> None:
    """Send an interactive reply-button message.

    Renders a message with up to three quick-reply buttons beneath the body
    text.  The recipient taps a button and the resulting ``button_reply``
    event is delivered to the webhook.

    Args:
        phone: Recipient's phone number in E.164 format without the leading
            ``+`` (e.g. ``"15551234567"``).
        body_text: The message body displayed above the buttons (up to 1024
            characters).
        buttons: A list of button descriptors.  Each dict must contain:

            * ``"id"``    — Unique identifier returned in the ``button_reply``
              event (≤256 chars).
            * ``"title"`` — Button label shown to the user (≤20 chars).

            Example::

                [
                    {"id": "opt_yes", "title": "Yes, please"},
                    {"id": "opt_no",  "title": "No thanks"},
                ]

            The Cloud API accepts a maximum of **3** buttons per message.

    Raises:
        httpx.HTTPStatusError: If the Cloud API returns a non-2xx response.
    """
    wa_buttons = [
        {
            "type": "reply",
            "reply": {
                "id": btn["id"],
                "title": btn["title"],
            },
        }
        for btn in buttons
    ]

    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": wa_buttons},
        },
    }

    log.info("whatsapp_send_buttons", phone=phone, button_count=len(buttons))

    async with httpx.AsyncClient() as client:
        response = await client.post(
            _messages_url(),
            json=payload,
            headers=_auth_headers(),
        )
        response.raise_for_status()

    log.debug(
        "whatsapp_send_buttons_ok",
        phone=phone,
        status_code=response.status_code,
    )
