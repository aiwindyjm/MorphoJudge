"""FastAPI daemon: health, analysis API (API-001/002) and worker wiring.

Startup (lifespan): migrate SQLite inside the data dir, auto-register git
repositories directly under the allowed roots, start the analysis runner and
re-enqueue non-terminal analyses (crash recovery). The API only accepts
registered repository ids — never raw paths or commands.

Error privacy (B04-R1-06): validation failures never echo the offending
input or arbitrary context back; unknown routes/methods answer with the
unified error envelope instead of a bare detail string.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import SERVICE_NAME, __version__
from .api.routes import analyses, results
from .contracts.errors import (
    ErrorCode,
    MorphoJudgeError,
    http_status_for,
)
from .db import database
from .db.repository import AnalysisRepository
from .git.snapshot import repository_id_for
from .settings import DaemonSettings, load_daemon_settings
from .worker.analysis import AnalysisRunner, AnalysisWorker


def register_root_repositories(
    repository: AnalysisRepository, roots: tuple[Path, ...]
) -> int:
    """Register git repositories located directly under the allowed roots.

    Read-only discovery of pre-mounted repos: no download, no network, no
    execution. The registry is the only path source the API ever exposes.
    """

    registered = 0
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not (child / ".git").is_dir():
                continue
            canonical = str(child.resolve())
            repository.register_repository(
                repository_id_for(canonical), child.name, canonical
            )
            registered += 1
    return registered


def sanitized_validation_details(exc: RequestValidationError) -> dict:
    """验证错误只暴露字段位置与错误类型；不回显 input/ctx 原文。"""

    items = []
    for error in exc.errors()[:20]:
        items.append(
            {
                "loc": [str(part) for part in error.get("loc", [])][:5],
                "type": str(error.get("type", "unknown")),
            }
        )
    return {"errors": items}


def create_app(settings: DaemonSettings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        active = settings or load_daemon_settings()
        active.data_dir.mkdir(parents=True, exist_ok=True)
        database.migrate(active.db_path)
        repository = AnalysisRepository(active.db_path)
        register_root_repositories(repository, active.repository_roots)
        worker = AnalysisWorker(
            repository,
            allowed_roots=active.repository_roots,
            manifest_dir=active.manifest_dir,
        )
        runner = AnalysisRunner(worker, concurrency=active.worker_concurrency)
        app.state.settings = active
        app.state.repository = repository
        app.state.runner = runner
        runner.recover()
        yield
        runner.shutdown(wait=True)

    app = FastAPI(title=SERVICE_NAME, version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": SERVICE_NAME, "version": __version__, "status": "ok"}

    app.include_router(analyses.router)
    app.include_router(results.router)

    @app.exception_handler(MorphoJudgeError)
    async def _morphojudge_error(_: Request, exc: MorphoJudgeError) -> JSONResponse:
        response = exc.to_response()
        return JSONResponse(
            status_code=http_status_for(exc.code),
            content=response.model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        error = MorphoJudgeError(
            ErrorCode.INVALID_INPUT,
            "request payload failed schema validation",
            retryable=False,
            details=sanitized_validation_details(exc),
        )
        return JSONResponse(
            status_code=http_status_for(error.code),
            content=error.to_response().model_dump(mode="json"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 未知路由/方法也走统一错误 envelope（B04-R1-05）。
        code = ErrorCode.ROUTE_NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_INPUT
        error = MorphoJudgeError(
            code,
            f"request did not match any API route (status {exc.status_code})",
            retryable=False,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error.to_response().model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def _internal(_: Request, exc: Exception) -> JSONResponse:
        error = MorphoJudgeError(
            ErrorCode.INTERNAL_ERROR,
            f"internal error: {type(exc).__name__}",
            retryable=True,
        )
        return JSONResponse(
            status_code=http_status_for(error.code),
            content=error.to_response().model_dump(mode="json"),
        )

    return app


app = create_app()
