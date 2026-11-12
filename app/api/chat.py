"""Local demonstration endpoints; a UUID is a conversation key, not an identity."""

from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import get_settings
from app.schemas.chat import ChatRequest, ChatResponse, ConfirmRequest
from app.services.chat_service import ChatError, ChatService


def require_local_chat(request: Request) -> None:
    try:
        peer = ip_address(request.client.host) if request.client else None
        local_client = peer is not None and (
            peer.is_loopback
            or any(peer in network for network in get_settings().chat_allowed_networks)
        )
        host = request.url.hostname
        local_host = host == "localhost" or (host and ip_address(host).is_loopback)
        origin = request.headers.get("origin")
        same_origin = not origin or (
            urlsplit(origin).netloc == request.url.netloc
            and urlsplit(origin).scheme == request.url.scheme
        )
    except ValueError:
        local_client = local_host = same_origin = False
    if not (local_client and local_host and same_origin):
        raise HTTPException(403, "Czat demonstracyjny jest dostępny tylko lokalnie.")


router = APIRouter(
    prefix="/api/v1/chat", tags=["chat"], dependencies=[Depends(require_local_chat)]
)


def get_chat_service(request: Request) -> ChatService:
    return request.state.chat_service


@router.post("", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(
    body: ChatRequest, service: Annotated[ChatService, Depends(get_chat_service)]
) -> ChatResponse:
    try:
        return await service.message(body)
    except ChatError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.post("/confirm", response_model=ChatResponse, response_model_exclude_none=True)
async def confirm(
    body: ConfirmRequest, service: Annotated[ChatService, Depends(get_chat_service)]
) -> ChatResponse:
    try:
        return await service.confirm(body)
    except ChatError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
