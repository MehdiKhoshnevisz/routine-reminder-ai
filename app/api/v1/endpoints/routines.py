from fastapi import APIRouter, HTTPException

from app.api.deps import DB, CurrentUser
from app.schemas.routine import RoutineCreate, RoutineRead, RoutineUpdate
from app.services import routine_service as rs

router = APIRouter(prefix="/users/{user_id}/routines", tags=["routines"])


@router.get("", response_model=list[RoutineRead])
async def list_routines(user: CurrentUser, db: DB):
    return await rs.list_routines(db, user.id)


@router.post("", response_model=RoutineRead, status_code=201)
async def create_routine(body: RoutineCreate, user: CurrentUser, db: DB):
    return await rs.create_routine(
        db, user.id, title=body.title, at=body.time, days=body.days, description=body.description
    )


async def _get(db, user_id: int, routine_id: int):
    r = await rs.get_routine(db, user_id, routine_id)
    if not r:
        raise HTTPException(404, "Routine not found")
    return r


@router.get("/{routine_id}", response_model=RoutineRead)
async def get_routine(routine_id: int, user: CurrentUser, db: DB):
    return await _get(db, user.id, routine_id)


@router.patch("/{routine_id}", response_model=RoutineRead)
async def update_routine(routine_id: int, body: RoutineUpdate, user: CurrentUser, db: DB):
    r = await _get(db, user.id, routine_id)
    return await rs.update_routine(db, r, **body.model_dump(exclude_unset=True))


@router.delete("/{routine_id}", status_code=204)
async def delete_routine(routine_id: int, user: CurrentUser, db: DB):
    await rs.delete_routine(db, await _get(db, user.id, routine_id))
