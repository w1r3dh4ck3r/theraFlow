"""WhatsApp Cloud API integration layer."""

from fastapi import APIRouter

# Routes are registered in the sub-modules that will be added in subsequent
# tasks (webhook handler, message sender, etc.).
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
