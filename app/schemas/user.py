from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    name: str
    telegram_id: int | None = None
    timezone: str | None = None


class UserUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    telegram_id: int | None
    timezone: str
    created_at: datetime
