from fastapi import APIRouter, HTTPException

from app.agent import agent
from app.api.deps import DB
from app.schemas.agent import ChatRequest, ChatResponse
from app.services import user_service

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, db: DB):
    user = await user_service.get_user(db, body.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    try:
        reply = await agent.chat(db, user, body.message)
    except ConnectionError as e:
        raise HTTPException(503, f"LLM unavailable: {e}")
    return ChatResponse(reply=reply)
