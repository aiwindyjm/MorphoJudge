"""FastAPI daemon skeleton (OPS-000): health endpoint plus unified errors.

No business endpoints in Batch-01; Git/selection services are exposed to
tests as Python functions only.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import SERVICE_NAME, __version__
from .contracts.errors import (
    ErrorCode,
    MorphoJudgeError,
    http_status_for,
)


def create_app() -> FastAPI:
    app = FastAPI(title=SERVICE_NAME, version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": SERVICE_NAME, "version": __version__, "status": "ok"}

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
            details={"errors": [str(item) for item in exc.errors()][:20]},
        )
        return JSONResponse(
            status_code=http_status_for(error.code),
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
