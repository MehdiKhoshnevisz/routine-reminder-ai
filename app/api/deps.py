from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import User
from app.services import user_service

DB = Annotated[AsyncSession, Depends(get_db)]


async def get_user_or_404(user_id: int, db: DB) -> User:
    user = await user_service.get_user(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


CurrentUser = Annotated[User, Depends(get_user_or_404)]
