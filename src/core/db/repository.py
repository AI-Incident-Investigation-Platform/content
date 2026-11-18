from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db.models import (
    Attachment,
    AuditEvent,
    Document,
    DocumentLink,
    DocumentVersion,
    Export,
    File,
    Quota,
)


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_document(self, document_id: UUID) -> Document | None:
        return await self.s.get(Document, document_id)

    async def get_version(self, document_id: UUID, version: int) -> DocumentVersion | None:
        return await self.s.get(DocumentVersion, (document_id, version))

    async def list_versions(self, document_id: UUID) -> list[DocumentVersion]:
        return list(
            (
                await self.s.scalars(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document_id)
                    .order_by(DocumentVersion.version.desc())
                )
            ).all()
        )

    async def list_links(self, document_id: UUID) -> list[DocumentLink]:
        return list(
            (
                await self.s.scalars(
                    select(DocumentLink).where(DocumentLink.document_id == document_id)
                )
            ).all()
        )

    async def list_documents(
        self,
        project_id: UUID,
        kind: str | None,
        target_type: str | None,
        target_id: UUID | None,
        cursor: UUID | None,
    ) -> list[Document]:
        query = select(Document).where(Document.project_id == project_id)
        if kind:
            query = query.where(Document.kind == kind)
        if target_type and target_id:
            query = query.join(DocumentLink).where(
                DocumentLink.target_type == target_type, DocumentLink.target_id == target_id
            )
        if cursor:
            query = query.where(Document.id > cursor)
        return list((await self.s.scalars(query.order_by(Document.id).limit(51))).all())

    async def create_document(
        self,
        project_id: UUID,
        actor: UUID,
        kind: str,
        title: str,
        body: str,
        links: list[tuple[str, UUID]],
    ) -> Document:
        doc = Document(
            id=uuid4(),
            project_id=project_id,
            kind=kind,
            title=title,
            current_version=1,
            created_by=actor,
            updated_by=actor,
        )
        self.s.add(doc)
        self.s.add(
            DocumentVersion(document_id=doc.id, version=1, title=title, body=body, author_id=actor)
        )
        for target_type, target_id in links:
            self.s.add(
                DocumentLink(document_id=doc.id, target_type=target_type, target_id=target_id)
            )
        await self.s.flush()
        return doc

    async def patch_document(
        self, doc: Document, expected: int, actor: UUID, title: str | None, body: str | None
    ) -> bool:
        old = await self.get_version(doc.id, expected)
        if old is None:
            return False
        result = await self.s.execute(
            update(Document)
            .where(Document.id == doc.id, Document.current_version == expected)
            .values(
                title=title or doc.title,
                current_version=expected + 1,
                updated_by=actor,
                updated_at=datetime.now(UTC),
            )
        )
        if getattr(result, "rowcount", 0) != 1:
            return False
        self.s.add(
            DocumentVersion(
                document_id=doc.id,
                version=expected + 1,
                title=title or old.title,
                body=body if body is not None else old.body,
                author_id=actor,
            )
        )
        await self.s.flush()
        return True

    async def delete_document(self, doc: Document, actor: UUID) -> None:
        self.s.add(
            AuditEvent(
                project_id=doc.project_id,
                actor_id=actor,
                action="document.deleted",
                resource_id=doc.id,
                data={"version": doc.current_version},
            )
        )
        await self.s.execute(
            delete(Attachment).where(
                Attachment.target_type == "document", Attachment.target_id == doc.id
            )
        )
        await self.s.delete(doc)

    async def add_link(self, doc_id: UUID, target_type: str, target_id: UUID) -> bool:
        result = await self.s.execute(
            insert(DocumentLink)
            .values(document_id=doc_id, target_type=target_type, target_id=target_id)
            .on_conflict_do_nothing()
        )
        return getattr(result, "rowcount", 0) == 1

    async def remove_link(self, doc_id: UUID, target_type: str, target_id: UUID) -> bool:
        result = await self.s.execute(
            delete(DocumentLink).where(
                DocumentLink.document_id == doc_id,
                DocumentLink.target_type == target_type,
                DocumentLink.target_id == target_id,
            )
        )
        return getattr(result, "rowcount", 0) == 1

    async def get_file(self, file_id: UUID, lock: bool = False) -> File | None:
        if lock:
            return (
                await self.s.scalars(select(File).where(File.id == file_id).with_for_update())
            ).one_or_none()
        return await self.s.get(File, file_id)

    async def list_attachments(self, file_id: UUID) -> list[Attachment]:
        return list(
            (await self.s.scalars(select(Attachment).where(Attachment.file_id == file_id))).all()
        )

    async def attach(self, file_id: UUID, target_type: str, target_id: UUID, actor: UUID) -> None:
        await self.s.execute(
            insert(Attachment)
            .values(
                file_id=file_id, target_type=target_type, target_id=target_id, attached_by=actor
            )
            .on_conflict_do_nothing()
        )

    async def detach(self, file_id: UUID, target_type: str, target_id: UUID) -> None:
        await self.s.execute(
            delete(Attachment).where(
                Attachment.file_id == file_id,
                Attachment.target_type == target_type,
                Attachment.target_id == target_id,
            )
        )

    async def reserve(
        self, project_id: UUID, actor: UUID, size: int, project_limit: int, user_limit: int
    ) -> bool:
        scopes = [("project", project_id, project_limit), ("user", actor, user_limit)]
        for scope, owner, _ in scopes:
            await self.s.execute(
                insert(Quota)
                .values(
                    project_id=project_id,
                    scope=scope,
                    owner_id=owner,
                    used_bytes=0,
                    reserved_bytes=0,
                )
                .on_conflict_do_nothing()
            )
        for scope, owner, limit in scopes:
            result = await self.s.execute(
                update(Quota)
                .where(
                    Quota.project_id == project_id,
                    Quota.scope == scope,
                    Quota.owner_id == owner,
                    Quota.used_bytes + Quota.reserved_bytes + size <= limit,
                )
                .values(reserved_bytes=Quota.reserved_bytes + size)
            )
            if getattr(result, "rowcount", 0) != 1:
                return False
        return True

    async def release(self, file: File, ready: bool = False) -> None:
        for scope, owner in (("project", file.project_id), ("user", file.uploaded_by)):
            values = {"reserved_bytes": Quota.reserved_bytes - file.size}
            if ready:
                values["used_bytes"] = Quota.used_bytes + file.size
            await self.s.execute(
                update(Quota)
                .where(
                    Quota.project_id == file.project_id,
                    Quota.scope == scope,
                    Quota.owner_id == owner,
                )
                .values(**values)
            )

    async def delete_file(self, file: File) -> None:
        for scope, owner in (("project", file.project_id), ("user", file.uploaded_by)):
            col = Quota.used_bytes if file.status == "ready" else Quota.reserved_bytes
            await self.s.execute(
                update(Quota)
                .where(
                    Quota.project_id == file.project_id,
                    Quota.scope == scope,
                    Quota.owner_id == owner,
                )
                .values({col: col - file.size})
            )
        await self.s.delete(file)

    async def pending_expired(self) -> list[File]:
        return list(
            (
                await self.s.scalars(
                    select(File)
                    .where(
                        File.status == "pending",
                        File.created_at < datetime.now(UTC) - timedelta(days=1),
                    )
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )

    async def get_export(self, export_id: UUID, lock: bool = False) -> Export | None:
        if lock:
            return (
                await self.s.scalars(select(Export).where(Export.id == export_id).with_for_update())
            ).one_or_none()
        return await self.s.get(Export, export_id)

    async def delete_project(self, project_id: UUID) -> list[File]:
        files = list(
            (await self.s.scalars(select(File).where(File.project_id == project_id))).all()
        )
        await self.s.execute(delete(Export).where(Export.project_id == project_id))
        await self.s.execute(delete(File).where(File.project_id == project_id))
        await self.s.execute(delete(Document).where(Document.project_id == project_id))
        await self.s.execute(delete(Quota).where(Quota.project_id == project_id))
        return files
