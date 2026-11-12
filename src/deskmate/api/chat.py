from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from deskmate.api.deps import ChatServiceDep, require_api_key
from deskmate.llm.base import LLMError

router = APIRouter()


class ChatRequest(BaseModel):
    user_id: Annotated[str, Field(min_length=1, max_length=64)]
    text: str

    @field_validator("text")
    @classmethod
    def _trim_and_check_length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 4000:
            raise ValueError("text must be 1 to 4000 characters after trimming")
        return value


class ChatResponse(BaseModel):
    answer: str
    dialog_id: int
    message_id: int
    cached: bool


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def chat(body: ChatRequest, service: ChatServiceDep) -> ChatResponse:
    try:
        result = await service.handle_message(f"api:{body.user_id}", body.text)
    except LLMError:
        raise HTTPException(status_code=502, detail="LLM unavailable") from None
    return ChatResponse(
        answer=result.text,
        dialog_id=result.dialog_id,
        message_id=result.message_id,
        cached=result.cached,
    )
