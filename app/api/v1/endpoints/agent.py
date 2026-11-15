import logging

from fastapi import APIRouter, HTTPException
from openai import APITimeoutError, AuthenticationError, OpenAIError, RateLimitError

from app.agent import agent
from app.api.deps import DB
from app.schemas.agent import ChatRequest, ChatResponse
from app.services import user_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, db: DB):
    user = await user_service.get_user(db, body.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    try:
        reply = await agent.chat(db, user, body.message)
    except RateLimitError:
        log.warning("OpenRouter rate limited")
        raise HTTPException(429, "LLM rate limited, try again shortly")
    except APITimeoutError:
        log.warning("OpenRouter request timed out")
        raise HTTPException(504, "LLM timed out")
    except AuthenticationError:
        log.error("OpenRouter auth failed, check OPENROUTER_API_KEY")
        raise HTTPException(503, "LLM unavailable")
    except OpenAIError:
        log.exception("LLM call failed")
        raise HTTPException(503, "LLM unavailable")
    return ChatResponse(reply=reply)
