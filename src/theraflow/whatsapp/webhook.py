"""WhatsApp Cloud API webhook handler.

Handles the two endpoints required by Meta's Webhooks product:

* ``GET /webhook/whatsapp``  — one-time verification challenge during setup.
* ``POST /webhook/whatsapp`` — inbound events (messages, statuses, etc.).

Signature validation uses HMAC-SHA256 over the raw request body with the
``WHATSAPP_APP_SECRET`` as the key, exactly as Meta specifies.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from theraflow.config import settings
from theraflow.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/webhook/whatsapp", tags=["whatsapp"])


# ---------------------------------------------------------------------------
# GET — Meta verification challenge
# ---------------------------------------------------------------------------


@router.get(
    "",
    summary="Meta webhook verification challenge",
    response_class=PlainTextResponse,
)
async def verify_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    """Respond to Meta's one-time webhook verification challenge.

    Meta sends ``hub.mode=subscribe``, a ``hub.verify_token``, and a
    ``hub.challenge`` integer.  We validate the token against config and echo
    the challenge back as plain text so Meta can confirm ownership.

    Args:
        hub_mode: Must be ``"subscribe"`` for a valid challenge request.
        hub_verify_token: Secret token configured in the Meta app dashboard.
        hub_challenge: Opaque value that must be returned verbatim.

    Returns:
        The ``hub.challenge`` value as a plain-text HTTP 200 response.

    Raises:
        HTTPException: 400 if ``hub.mode`` is not ``"subscribe"``.
        HTTPException: 403 if the verify token does not match config.
    """
    if hub_mode != "subscribe":
        log.warning("webhook_verify_bad_mode", hub_mode=hub_mode)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="hub.mode must be 'subscribe'",
        )
    if hub_verify_token != settings.whatsapp_verify_token:
        log.warning("webhook_verify_token_mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify token mismatch",
        )

    log.info("webhook_verified")
    return PlainTextResponse(content=hub_challenge or "")


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Raise HTTP 403 if the ``X-Hub-Signature-256`` header is absent or wrong.

    Meta computes ``HMAC-SHA256(app_secret, raw_body)`` and sends it as
    ``sha256=<hex>``.  We do the same and compare with :func:`hmac.compare_digest`
    to prevent timing-attack leakage.

    Args:
        raw_body: The unmodified request body bytes.
        signature_header: Value of the ``X-Hub-Signature-256`` header.

    Raises:
        HTTPException: 403 if the header is missing, malformed, or the digest
            does not match.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        log.warning("webhook_signature_missing")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or malformed X-Hub-Signature-256 header",
        )

    expected = hmac.new(
        settings.whatsapp_app_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected, received):
        log.warning("webhook_signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signature verification failed",
        )


# ---------------------------------------------------------------------------
# Payload parsing helpers
# ---------------------------------------------------------------------------


def _extract_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every ``messages`` object found in a webhook payload.

    A single POST can contain multiple entries and changes; this flattens
    them into a single list so the caller can iterate without nested loops.

    Args:
        payload: The parsed JSON body from Meta's webhook POST.

    Returns:
        A flat list of message objects (may be empty for status-only events).
    """
    messages: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages.extend(value.get("messages", []))
    return messages


# ---------------------------------------------------------------------------
# POST — inbound events
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="Receive inbound WhatsApp events",
)
async def receive_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Process inbound webhook events from the Meta Cloud API.

    Flow:
    1. Read raw body and validate ``X-Hub-Signature-256`` HMAC.
    2. Parse JSON payload.
    3. Skip events that contain no ``messages`` (e.g. delivery status updates).
    4. For each message dispatch to the conversation engine:
       * ``text`` — plain SMS-style messages.
       * ``interactive`` / ``button_reply`` — quick-reply button taps.
       * All other types are logged and silently ignored.

    Args:
        request: The raw FastAPI request (needed to read the body before JSON
            parsing so signature verification sees the exact bytes Meta signed).
        x_hub_signature_256: HMAC-SHA256 signature injected by FastAPI from the
            ``X-Hub-Signature-256`` HTTP header.

    Returns:
        ``{"status": "ok"}`` — Meta requires a 200 response to stop retries.
    """
    raw_body = await request.body()
    _verify_signature(raw_body, x_hub_signature_256)

    payload: dict[str, Any] = await request.json()

    messages = _extract_messages(payload)
    if not messages:
        log.debug("webhook_no_messages", object_type=payload.get("object"))
        return {"status": "ok"}

    for message in messages:
        msg_type = message.get("type")
        sender: str | None = message.get("from")

        if msg_type == "text":
            text: str = message.get("text", {}).get("body", "")
            log.info(
                "whatsapp_inbound_text",
                sender=sender,
                length=len(text),
            )
            await _dispatch(sender=sender, text=text, button_payload=None)

        elif msg_type == "interactive":
            interactive = message.get("interactive", {})
            button_reply: dict[str, Any] = interactive.get("button_reply", {})
            log.info(
                "whatsapp_inbound_button_reply",
                sender=sender,
                button_id=button_reply.get("id"),
                button_title=button_reply.get("title"),
            )
            await _dispatch(sender=sender, text=None, button_payload=button_reply)

        else:
            log.debug("whatsapp_inbound_type_ignored", sender=sender, msg_type=msg_type)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Conversation engine adapter
# ---------------------------------------------------------------------------


async def _dispatch(
    *,
    sender: str | None,
    text: str | None,
    button_payload: dict[str, Any] | None,
) -> None:
    """Forward a parsed inbound message to the conversation engine.

    This is intentionally a thin adapter.  When the conversation engine
    module is implemented it will be wired in here, keeping the webhook
    handler free of business logic.

    Args:
        sender: Sender's phone number (E.164, no ``+``).
        text: Message body for plain-text messages; ``None`` for button replies.
        button_payload: Parsed ``button_reply`` dict for interactive messages;
            ``None`` for plain-text messages.
    """
    # TODO: replace with real conversation engine call, e.g.:
    #   await conversation.handle_message(
    #       sender=sender, text=text, button_payload=button_payload
    #   )
    log.debug(
        "whatsapp_dispatch",
        sender=sender,
        text=text,
        button_payload=button_payload,
    )
