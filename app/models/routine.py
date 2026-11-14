from datetime import date, datetime, time as dtime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, SmallInteger, String, Text, Time, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Routine(Base):
    __tablename__ = "routines"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    time: Mapped[dtime] = mapped_column(Time)  # local time in the user's timezone
    # Weekdays 0=Monday ... 6=Sunday. Empty list = every day.
    days: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_reminded_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="routines")  # noqa: F821
