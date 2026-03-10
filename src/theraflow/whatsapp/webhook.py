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

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from theraflow.config import settings
from theraflow.conversation.engine import ConversationEngine, OutgoingMessage
from theraflow.logging import get_logger
from theraflow.utils import mask_phone
from theraflow.whatsapp.sender import send_button_message, send_text_message

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


def _extract_contact_names(payload: dict[str, Any]) -> dict[str, str]:
    """Return a mapping of WhatsApp ID → display name from a webhook payload.

    Meta includes a ``contacts`` array alongside the ``messages`` array inside
    each change value.  Each contact has a ``wa_id`` (the phone number) and a
    ``profile.name`` field.

    Args:
        payload: The parsed JSON body from Meta's webhook POST.

    Returns:
        Dict mapping ``wa_id`` strings to display name strings.  Values may
        be empty strings if the profile name was not provided.
    """
    names: dict[str, str] = {}
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for contact in value.get("contacts", []):
                wa_id: str = contact.get("wa_id", "")
                name: str = contact.get("profile", {}).get("name", "")
                if wa_id:
                    names[wa_id] = name
    return names


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

    contact_names = _extract_contact_names(payload)
    engine: ConversationEngine = request.app.state.engine
    http_client: httpx.AsyncClient = request.app.state.http_client

    for message in messages:
        try:
            msg_type = message.get("type")
            sender: str | None = message.get("from")
            name: str = contact_names.get(sender or "", "")

            if msg_type == "text":
                text: str = message.get("text", {}).get("body", "")
                log.info(
                    "whatsapp_inbound_text",
                    sender=mask_phone(sender),
                    length=len(text),
                )
                await _dispatch(
                    engine=engine,
                    sender=sender,
                    name=name,
                    text=text,
                    button_payload=None,
                    http_client=http_client,
                )

            elif msg_type == "interactive":
                interactive = message.get("interactive", {})
                button_reply: dict[str, Any] = interactive.get("button_reply", {})
                log.info(
                    "whatsapp_inbound_button_reply",
                    sender=mask_phone(sender),
                    button_id=button_reply.get("id"),
                    button_title=button_reply.get("title"),
                )
                await _dispatch(
                    engine=engine,
                    sender=sender,
                    name=name,
                    text=None,
                    button_payload=button_reply,
                    http_client=http_client,
                )

            else:
                log.debug("whatsapp_inbound_type_ignored", sender=mask_phone(sender), msg_type=msg_type)

        except Exception:
            log.exception(
                "webhook_processing_error",
                message_id=message.get("id"),
                sender=mask_phone(message.get("from")),
                msg_type=message.get("type"),
            )
            continue

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Conversation engine adapter
# ---------------------------------------------------------------------------


async def _dispatch(
    *,
    engine: ConversationEngine,
    sender: str | None,
    name: str,
    text: str | None,
    button_payload: dict[str, Any] | None,
    http_client: httpx.AsyncClient,
) -> None:
    """Forward a parsed inbound message to the conversation engine.

    This thin adapter keeps the webhook handler free of business logic.
    It delegates to :func:`~theraflow.conversation.engine.ConversationEngine.handle_message`
    and sends each returned :class:`~theraflow.conversation.engine.OutgoingMessage`
    via the appropriate WhatsApp sender function.

    Args:
        engine: The application-wide :class:`~theraflow.conversation.engine.ConversationEngine`
            instance retrieved from ``app.state``.
        sender: Sender's phone number (E.164, no ``+``).
        name: WhatsApp display name for the contact (may be empty string).
        text: Message body for plain-text messages; ``None`` for button replies.
        button_payload: Parsed ``button_reply`` dict for interactive messages;
            ``None`` for plain-text messages.
        http_client: Shared :class:`httpx.AsyncClient` from ``app.state``.
    """
    if not sender:
        log.warning("whatsapp_dispatch_no_sender")
        return

    log.debug(
        "whatsapp_dispatch",
        sender=mask_phone(sender),
        text=text,
        button_payload=button_payload,
    )

    outgoing: list[OutgoingMessage] = await engine.handle_message(
        phone=sender,
        name=name,
        text=text,
        button_payload=button_payload,
    )

    for msg in outgoing:
        try:
            if msg.is_interactive:
                await send_button_message(sender, msg.text, msg.buttons, http_client=http_client)
            else:
                await send_text_message(sender, msg.text, http_client=http_client)
        except Exception:
            log.exception("whatsapp_send_failed", sender=mask_phone(sender))
