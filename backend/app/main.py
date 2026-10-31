import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.database.session import Base, engine
from app.models import models as _models  # noqa: F401  (registers all SQLAlchemy models on Base)
from app.routers import admin, admin_progress, auth, media, practice, progress

logger = logging.getLogger("verba")

Base.metadata.create_all(bind=engine)

DEFAULT_CORS_ORIGINS = (
    "https://verba-spokenenglishassistant.onrender.com",
    "https://verba-frontend.onrender.com",
    "http://localhost:3000",
)


def _build_cors_origins() -> list[str]:
    configured = [x.strip().rstrip("/") for x in (settings.cors_origins or "").split(",") if x.strip()]
    origins: list[str] = []
    for origin in [*configured, *DEFAULT_CORS_ORIGINS]:
        if origin and origin not in origins:
            origins.append(origin)
    return origins


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                headers = MutableHeaders(scope=message)
                headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
            await send(message)

        await self.app(scope, receive, send_wrapper)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "http_request method=%s path=%s status=%d duration_ms=%.2f",
            scope.get("method", ""),
            scope.get("path", ""),
            status_code,
            elapsed_ms,
        )


app = FastAPI(
    title="Verba API",
    version="0.1.0",
    description="REST API for English speaking practice.",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time-Ms"],
)


app.include_router(auth.router)
app.include_router(media.router)
app.include_router(practice.router)
app.include_router(progress.router)
app.include_router(admin.router)
app.include_router(admin_progress.router)

if settings.jwt_secret == "development-only-change-me":
    logger.warning(
        "JWT_SECRET is the built-in development default: anyone can forge tokens. Set a long random JWT_SECRET."
    )


@app.get("/", tags=["system"])
def root():
    return {
        "status": "online",
        "application": "Verba",
        "message": "Verba backend is running",
    }


@app.get("/health", tags=["system"])
def health():
    return {"status": "healthy"}


@app.get("/lessons", tags=["lessons"])
def lessons():
    return []
