import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.books import router as books_router
from app.api.chat import router as chat_router
from app.api.exception_handlers import register_exception_handlers
from app.api.health import router as health_router
from app.core.config import get_settings
from app.services.chat_service import ChatService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield {"chat_service": ChatService()}


app = FastAPI(
    title=get_settings().app_name,
    description="A REST API for managing library books.",
    version="0.1.0",
    lifespan=lifespan,
)
register_exception_handlers(app)
app.include_router(health_router)
app.include_router(books_router)
app.include_router(chat_router)
