from fastapi import FastAPI

from app.api.books import router as books_router
from app.api.exception_handlers import register_exception_handlers
from app.api.health import router as health_router
from app.core.config import get_settings

app = FastAPI(
    title=get_settings().app_name,
    description="A REST API for managing library books.",
    version="0.1.0",
)
register_exception_handlers(app)
app.include_router(health_router)
app.include_router(books_router)
