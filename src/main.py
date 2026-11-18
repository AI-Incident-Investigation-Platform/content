from aiip_kit.errors import (
    DependencyUnavailableError,
    NotAuthenticatedError,
    PermissionDeniedError,
    ProjectNotFoundError,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.router import Authenticate, build_router
from src.api.service import Conflict, ContentService, Missing, Stale


def create_app(service: ContentService, authenticate: Authenticate) -> FastAPI:
    app = FastAPI(title="AIIP content")
    app.include_router(build_router(service, authenticate))

    def handler(status: int, code: str):
        async def respond(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"error": {"code": code, "message": str(exc)}}, status_code=status)

        return respond

    for exc, status, code in (
        (Missing, 404, "not_found"),
        (ProjectNotFoundError, 404, "not_found"),
        (Conflict, 409, "conflict"),
        (Stale, 412, "stale_version"),
        (NotAuthenticatedError, 401, "unauthenticated"),
        (PermissionDeniedError, 403, "permission_denied"),
        (DependencyUnavailableError, 503, "dependency_unavailable"),
    ):
        app.add_exception_handler(exc, handler(status, code))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
