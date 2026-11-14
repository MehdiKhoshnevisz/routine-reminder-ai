from datetime import datetime, time as dtime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RoutineBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    time: dtime
    days: list[int] = Field(default_factory=list, description="0=Mon..6=Sun, empty = every day")

    @field_validator("days")
    @classmethod
    def _valid_days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days must be between 0 (Mon) and 6 (Sun)")
        return sorted(set(v))


class RoutineCreate(RoutineBase):
    pass


class RoutineUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    time: dtime | None = None
    days: list[int] | None = None
    is_active: bool | None = None


class RoutineRead(RoutineBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    is_active: bool
    created_at: datetime
