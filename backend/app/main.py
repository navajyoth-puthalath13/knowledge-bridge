"""Entry point: python -m app.main --port 8765

The Electron app starts this process with KB_TOKEN set and stops it on quit.
The server binds to 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import uvicorn
from fastapi import FastAPI

from .api.routes import router
from .config import data_dir
from .security.auth import TokenAuthMiddleware
from .service import Service


def setup_logging() -> None:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(d / "knowledge-bridge.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [fh, sh]
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(token: str, service: Service | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service or Service()
        yield
        app.state.service.shutdown()

    app = FastAPI(title="Knowledge Bridge", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TokenAuthMiddleware, token=token)
    app.include_router(router)
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("KB_PORT", "8765")))
    args = ap.parse_args()
    token = os.environ.get("KB_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        print("KB_TOKEN not set; generated a one-off token (only useful for manual testing):", token, file=sys.stderr)
    setup_logging()
    logging.getLogger(__name__).info("starting Knowledge Bridge service on 127.0.0.1:%d", args.port)
    # uvicorn handles SIGINT/SIGTERM and runs the lifespan shutdown (graceful stop of the sync job)
    uvicorn.run(create_app(token), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
