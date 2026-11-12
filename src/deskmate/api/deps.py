import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from deskmate.chat.service import ChatService


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def require_api_key(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected: str = request.app.state.settings.api_key
    # compare_digest takes bytes so non-ASCII header values cannot raise TypeError
    if x_api_key is None or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key")


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
