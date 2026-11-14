from fastapi import APIRouter, HTTPException

from app.api.deps import DB, CurrentUser
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
async def list_users(db: DB):
    return await user_service.list_users(db)


@router.post("", response_model=UserRead, status_code=201)
async def create_user(body: UserCreate, db: DB):
    try:
        return await user_service.create_user(db, **body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user: CurrentUser):
    return user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(body: UserUpdate, user: CurrentUser, db: DB):
    try:
        return await user_service.update_user(db, user, **body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
