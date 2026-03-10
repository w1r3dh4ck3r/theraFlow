"""Conversation state machine and flow management.

Exports :class:`~theraflow.conversation.engine.ConversationEngine` and
supporting types.  The application-wide engine instance is created during the
FastAPI lifespan in :mod:`theraflow.main` with injected
:class:`~theraflow.sheets.client.SheetsClient` and
:class:`~theraflow.notifications.telegram.TelegramNotifier` dependencies, and
stored on ``app.state.engine``.
"""

from theraflow.conversation.engine import ConversationEngine, OutgoingMessage, UserSession
from theraflow.conversation.flow import Step, StepConfig

__all__ = [
    "ConversationEngine",
    "OutgoingMessage",
    "Step",
    "StepConfig",
    "UserSession",
]
