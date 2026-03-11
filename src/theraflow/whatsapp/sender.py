"""WhatsApp Cloud API message sender.

Provides async helpers for sending outbound messages via the Meta WhatsApp
Cloud API (v21.0).  All functions accept a shared ``httpx.AsyncClient`` and
log every send with structlog.

Usage::

    from theraflow.whatsapp.sender import send_text_message, send_button_message

    await send_text_message("+15551234567", "Hello!", http_client=client)
    await send_button_message(
        "+15551234567",
        body_text="Are you ready?",
        buttons=[{"id": "yes", "title": "Yes"}, {"id": "no", "title": "No"}],
        http_client=client,
    )
"""

from __future__ import annotations

import httpx

from theraflow.config import settings
from theraflow.logging import get_logger
from theraflow.utils import mask_phone

log = get_logger(__name__)

_API_BASE = "https://graph.facebook.com"
_API_VERSION = "v21.0"


def _normalize_br_phone(phone: str) -> str:
    """Normalize Brazilian mobile numbers for the WhatsApp Cloud API.

    WhatsApp webhook delivers Brazilian numbers as ``55XX9XXXXXXX`` (12 digits)
    but the Cloud API sometimes requires the full ``55XX9XXXXXXX`` (13 digits)
    format with the extra ``9`` prefix on the local number.  If a 12-digit
    Brazilian number is detected (55 + 2-digit DDD + 8-digit local), insert the
    ``9`` after the DDD.
    """
    if len(phone) == 12 and phone.startswith("55"):
        # 55 + DD + 8 digits → 55 + DD + 9 + 8 digits
        return phone[:4] + "9" + phone[4:]
    return phone


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


async def send_text_message(
    phone: str,
    text: str,
    *,
    http_client: httpx.AsyncClient,
) -> None:
    """Send a plain-text WhatsApp message.

    Args:
        phone: Recipient's phone number in E.164 format without the leading
            ``+`` (e.g. ``"15551234567"``), as required by the Cloud API.
        text: Message body (up to 4096 characters).
        http_client: A shared :class:`httpx.AsyncClient` instance managed by
            the application lifespan.

    Raises:
        httpx.HTTPStatusError: If the Cloud API returns a non-2xx response.
    """
    to = _normalize_br_phone(phone)
    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text,
        },
    }

    log.info("whatsapp_send_text", phone=mask_phone(phone), length=len(text))

    response = await http_client.post(
        _messages_url(),
        json=payload,
        headers=_auth_headers(),
    )
    response.raise_for_status()

    log.debug("whatsapp_send_text_ok", phone=mask_phone(phone), status_code=response.status_code)


async def send_button_message(
    phone: str,
    body_text: str,
    buttons: list[dict],
    *,
    http_client: httpx.AsyncClient,
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
        http_client: A shared :class:`httpx.AsyncClient` instance managed by
            the application lifespan.

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

    to = _normalize_br_phone(phone)
    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": wa_buttons},
        },
    }

    log.info("whatsapp_send_buttons", phone=mask_phone(phone), button_count=len(buttons))

    response = await http_client.post(
        _messages_url(),
        json=payload,
        headers=_auth_headers(),
    )
    response.raise_for_status()

    log.debug(
        "whatsapp_send_buttons_ok",
        phone=mask_phone(phone),
        status_code=response.status_code,
    )


async def send_list_message(
    phone: str,
    body_text: str,
    button_text: str,
    rows: list[dict],
    *,
    http_client: httpx.AsyncClient,
) -> None:
    """Send an interactive list message (up to 10 selectable rows).

    Args:
        phone: Recipient phone number (E.164, no ``+``).
        body_text: Message body displayed above the list button.
        button_text: Label on the button that opens the list (max 20 chars).
        rows: List of row dicts, each with ``"id"`` and ``"title"`` keys.
        http_client: Shared httpx client.
    """
    to = _normalize_br_phone(phone)
    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_text[:20],
                "sections": [
                    {
                        "title": "Opções",
                        "rows": [
                            {"id": row["id"], "title": row["title"][:24]}
                            for row in rows
                        ],
                    }
                ],
            },
        },
    }

    log.info("whatsapp_send_list", phone=mask_phone(phone), row_count=len(rows))

    response = await http_client.post(
        _messages_url(),
        json=payload,
        headers=_auth_headers(),
    )
    response.raise_for_status()

    log.debug(
        "whatsapp_send_list_ok",
        phone=mask_phone(phone),
        status_code=response.status_code,
    )
