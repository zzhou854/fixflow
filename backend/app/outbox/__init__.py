"""PostgreSQL-leased at-least-once outbox delivery."""

from app.outbox.dispatcher import OutboxDispatcher

__all__ = ["OutboxDispatcher"]
