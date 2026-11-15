"""Conversation state that used to live in memory; stored in the DB so it survives serverless restarts."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramState(Base):
    """The bot's per-user `context.user_data` (e.g. which field an edit is waiting for)."""

    __tablename__ = "telegram_states"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class AgentHistory(Base):
    """Last few messages of the agent conversation, so follow-ups like "move it to 8" have context."""

    __tablename__ = "agent_histories"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
