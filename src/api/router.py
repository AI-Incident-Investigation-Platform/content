from collections.abc import Awaitable, Callable
from uuid import UUID

from aiip_kit.access.model import Principal
from fastapi import APIRouter, Header, HTTPException, Response

from src.api.schemas import (
    DocumentCreate,
    DocumentKind,
    DocumentPatch,
    ExportCreate,
    TargetType,
    UploadCreate,
)
from src.api.service import ContentService

Authenticate = Callable[[str | None], Awaitable[Principal]]


def build_router(service: ContentService, authenticate: Authenticate) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    async def user(authorization: str | None = Header(default=None)) -> Principal:
        return await authenticate(authorization)

    from fastapi import Depends

    User = Depends(user)

    @router.get("/projects/{project_id}/documents")
    async def documents(
        project_id: UUID,
        principal: Principal = User,
        kind: DocumentKind | None = None,
        target_type: TargetType | None = None,
        target_id: UUID | None = None,
        cursor: UUID | None = None,
    ) -> dict:
        if (target_type is None) != (target_id is None):
            raise HTTPException(422, "target_type and target_id must be supplied together")
        return await service.list_documents(
            principal, project_id, kind, target_type, target_id, cursor
        )

    @router.post("/projects/{project_id}/documents", status_code=201)
    async def create_document(
        project_id: UUID, data: DocumentCreate, principal: Principal = User
    ) -> dict:
        return await service.create_document(principal, project_id, data)

    @router.get("/documents/{document_id}")
    async def document(document_id: UUID, response: Response, principal: Principal = User) -> dict:
        result = await service.get_document(principal, document_id)
        response.headers["ETag"] = str(result["version"])
        return result

    @router.patch("/documents/{document_id}")
    async def patch_document(
        document_id: UUID,
        data: DocumentPatch,
        principal: Principal = User,
        if_match: str | None = Header(default=None),
    ) -> dict:
        if if_match is None:
            raise HTTPException(428, "If-Match required")
        try:
            expected = int(if_match.strip('"'))
        except ValueError as exc:
            raise HTTPException(422, "If-Match must be a version") from exc
        return await service.patch_document(principal, document_id, expected, data)

    @router.delete("/documents/{document_id}", status_code=204)
    async def delete_document(document_id: UUID, principal: Principal = User) -> None:
        await service.delete_document(principal, document_id)

    @router.get("/documents/{document_id}/versions")
    async def versions(document_id: UUID, principal: Principal = User) -> dict:
        return await service.versions(principal, document_id)

    @router.get("/documents/{document_id}/versions/{version}")
    async def version(document_id: UUID, version: int, principal: Principal = User) -> dict:
        return await service.versions(principal, document_id, version)

    @router.put("/documents/{document_id}/links/{target_type}/{target_id}", status_code=204)
    async def link(
        document_id: UUID, target_type: str, target_id: UUID, principal: Principal = User
    ) -> None:
        if target_type not in {"service", "incident"}:
            raise HTTPException(422, "invalid target type")
        await service.link(principal, document_id, target_type, target_id)

    @router.delete("/documents/{document_id}/links/{target_type}/{target_id}", status_code=204)
    async def unlink(
        document_id: UUID, target_type: str, target_id: UUID, principal: Principal = User
    ) -> None:
        if target_type not in {"service", "incident"}:
            raise HTTPException(422, "invalid target type")
        await service.link(principal, document_id, target_type, target_id, remove=True)

    @router.post("/uploads", status_code=201)
    async def upload(data: UploadCreate, principal: Principal = User) -> dict:
        return await service.create_upload(principal, data)

    @router.post("/uploads/{file_id}/complete")
    async def complete(file_id: UUID, principal: Principal = User) -> dict:
        return await service.complete_upload(principal, file_id)

    @router.get("/files/{file_id}")
    async def file_link(file_id: UUID, principal: Principal = User) -> dict:
        return await service.file_link(principal, file_id)

    @router.delete("/files/{file_id}", status_code=204)
    async def delete_file(file_id: UUID, principal: Principal = User) -> None:
        await service.delete_file(principal, file_id)

    @router.put("/attachments/{target_type}/{target_id}/{file_id}", status_code=204)
    async def attach(
        target_type: str, target_id: UUID, file_id: UUID, principal: Principal = User
    ) -> None:
        if target_type not in {"incident", "review", "document"}:
            raise HTTPException(422, "invalid target type")
        await service.attachment(principal, target_type, target_id, file_id)

    @router.delete("/attachments/{target_type}/{target_id}/{file_id}", status_code=204)
    async def detach(
        target_type: str, target_id: UUID, file_id: UUID, principal: Principal = User
    ) -> None:
        if target_type not in {"incident", "review", "document"}:
            raise HTTPException(422, "invalid target type")
        await service.attachment(principal, target_type, target_id, file_id, remove=True)

    @router.post("/projects/{project_id}/exports", status_code=202)
    async def export(project_id: UUID, data: ExportCreate, principal: Principal = User) -> dict:
        return await service.create_export(principal, project_id, data)

    @router.get("/exports/{export_id}")
    async def export_status(export_id: UUID, principal: Principal = User) -> dict:
        return await service.get_export(principal, export_id)

    return router
