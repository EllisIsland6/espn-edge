"""FastAPI application entrypoint (SPEC 3)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db
from .routers import accounts, health, leagues


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ESPN Edge", version="0.1.0", lifespan=lifespan)

# Vite dev server talks to the API cross-origin in dev only.
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{_settings.web_port}",
        f"http://127.0.0.1:{_settings.web_port}",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(leagues.router)


@app.get("/")
def root() -> dict:
    return {"app": "espn-edge", "docs": "/docs", "health": "/api/health"}
