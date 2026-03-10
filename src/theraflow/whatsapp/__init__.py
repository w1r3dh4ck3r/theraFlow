"""WhatsApp Cloud API integration layer.

Exports
-------
router
    FastAPI ``APIRouter`` mounted at ``/webhook/whatsapp``.  Include this in
    the top-level FastAPI application::

        from theraflow.whatsapp import router as whatsapp_router
        app.include_router(whatsapp_router)

sender
    Async helpers for sending outbound messages:
    :func:`~theraflow.whatsapp.sender.send_text_message` and
    :func:`~theraflow.whatsapp.sender.send_button_message`.
"""

from theraflow.whatsapp.webhook import router

__all__ = ["router"]
