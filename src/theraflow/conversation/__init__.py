"""Conversation state machine and flow management.

Exports a singleton :class:`~theraflow.conversation.engine.ConversationEngine`
instance (``engine``) that is shared across the application.  The webhook
handler imports this instance to dispatch inbound messages.
"""

from theraflow.conversation.engine import ConversationEngine, OutgoingMessage, UserSession
from theraflow.conversation.flow import Step, StepConfig

#: Application-wide singleton.  Import this in the webhook handler.
engine = ConversationEngine()

__all__ = [
    "ConversationEngine",
    "OutgoingMessage",
    "Step",
    "StepConfig",
    "UserSession",
    "engine",
]
