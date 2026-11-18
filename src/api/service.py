from typing import Protocol
from uuid import UUID, uuid4

from aiip_kit.access.model import Principal
from aiip_kit.errors import TransientError
from aiip_kit.messaging.message import Actor, MessageV1
from aiip_kit.messaging.topology import Destination
from aiip_kit.outbox.inbox import InboxGuard
from aiip_kit.outbox.writer import OutboxWriter
from botocore.exceptions import BotoCoreError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.schemas import DocumentCreate, DocumentPatch, ExportCreate, UploadCreate
from src.core.db.models import Document, Export, File, inbox, outbox
from src.core.db.repository import Repository
from src.core.storage import ObjectStorage


class Missing(Exception):
    pass


class Conflict(Exception):
    pass


class Stale(Exception):
    pass


class AccessChecker(Protocol):
    async def require(self, principal: Principal, project_id: UUID, permission: str) -> object: ...


class ContentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        access: AccessChecker,
        storage: ObjectStorage,
        project_limit: int = 1073741824,
        user_limit: int = 268435456,
    ) -> None:
        self.sessions = sessions
        self.access = access
        self.storage = storage
        self.project_limit = project_limit
        self.user_limit = user_limit
        self.outbox = OutboxWriter(outbox)
        self.inbox = InboxGuard(inbox)

    async def _require(self, principal: Principal, project_id: UUID, permission: str) -> None:
        await self.access.require(principal, project_id, permission)

    async def _event(
        self,
        session: AsyncSession,
        kind: str,
        project_id: UUID,
        actor: UUID,
        data: dict,
        aggregate_id: UUID | None = None,
        version: int | None = None,
    ) -> None:
        msg = MessageV1.event(
            kind,
            "content",
            data,
            project_id=project_id,
            aggregate_type="document" if kind.startswith("document.") else None,
            aggregate_id=aggregate_id,
            aggregate_version=version,
            actor=Actor(kind="user", id=str(actor)),
        )
        await self.outbox.add(session, msg, Destination.event("content", kind))
        if kind.startswith("document."):
            await self.outbox.add(
                session,
                msg.caused(
                    kind,
                    "content",
                    data,
                    aggregate_type="document",
                    aggregate_id=aggregate_id,
                    aggregate_version=version,
                ),
                Destination.live("content", f"project.{project_id}"),
            )

    async def _document(
        self, repo: Repository, document_id: UUID, principal: Principal, permission: str
    ) -> Document:
        doc = await repo.get_document(document_id)
        if doc is None:
            raise Missing
        await self._require(principal, doc.project_id, permission)
        return doc

    async def _document_json(self, repo: Repository, doc: Document) -> dict:
        version = await repo.get_version(doc.id, doc.current_version)
        if version is None:
            raise Missing
        links = await repo.list_links(doc.id)
        return {
            "id": doc.id,
            "project_id": doc.project_id,
            "kind": doc.kind,
            "title": doc.title,
            "version": doc.current_version,
            "body": version.body,
            "links": [
                {"target_type": link.target_type, "target_id": link.target_id} for link in links
            ],
            "created_by": doc.created_by,
            "updated_by": doc.updated_by,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }

    async def create_document(
        self, principal: Principal, project_id: UUID, data: DocumentCreate
    ) -> dict:
        await self._require(principal, project_id, "document.write")
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            doc = await repo.create_document(
                project_id,
                principal.user_id,
                data.kind,
                data.title,
                data.body,
                [(link.target_type, link.target_id) for link in data.links],
            )
            await self._event(
                session,
                "document.created",
                project_id,
                principal.user_id,
                {"document_id": str(doc.id), "kind": doc.kind, "title": doc.title, "version": 1},
                doc.id,
                1,
            )
            return await self._document_json(repo, doc)

    async def get_document(self, principal: Principal, document_id: UUID) -> dict:
        async with self.sessions() as session:
            repo = Repository(session)
            return await self._document_json(
                repo, await self._document(repo, document_id, principal, "project.read")
            )

    async def list_documents(
        self,
        principal: Principal,
        project_id: UUID,
        kind: str | None,
        target_type: str | None,
        target_id: UUID | None,
        cursor: UUID | None,
    ) -> dict:
        await self._require(principal, project_id, "project.read")
        async with self.sessions() as session:
            docs = await Repository(session).list_documents(
                project_id, kind, target_type, target_id, cursor
            )
            items = [
                {
                    "id": d.id,
                    "project_id": d.project_id,
                    "kind": d.kind,
                    "title": d.title,
                    "version": d.current_version,
                    "updated_by": d.updated_by,
                    "updated_at": d.updated_at,
                }
                for d in docs[:50]
            ]
            return {"items": items, "next_cursor": str(docs[49].id) if len(docs) > 50 else None}

    async def patch_document(
        self, principal: Principal, document_id: UUID, expected: int, data: DocumentPatch
    ) -> dict:
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            doc = await self._document(repo, document_id, principal, "document.write")
            if not await repo.patch_document(
                doc, expected, principal.user_id, data.title, data.body
            ):
                raise Stale
            await session.refresh(doc)
            await self._event(
                session,
                "document.changed",
                doc.project_id,
                principal.user_id,
                {
                    "document_id": str(doc.id),
                    "kind": doc.kind,
                    "title": doc.title,
                    "version": doc.current_version,
                },
                doc.id,
                doc.current_version,
            )
            return await self._document_json(repo, doc)

    async def delete_document(self, principal: Principal, document_id: UUID) -> None:
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            doc = await self._document(repo, document_id, principal, "document.write")
            await self._event(
                session,
                "document.deleted",
                doc.project_id,
                principal.user_id,
                {
                    "document_id": str(doc.id),
                    "kind": doc.kind,
                    "title": doc.title,
                    "version": doc.current_version,
                },
                doc.id,
                doc.current_version,
            )
            await repo.delete_document(doc, principal.user_id)

    async def versions(
        self, principal: Principal, document_id: UUID, version: int | None = None
    ) -> dict:
        async with self.sessions() as session:
            repo = Repository(session)
            await self._document(repo, document_id, principal, "project.read")
            rows = (
                [await repo.get_version(document_id, version)]
                if version
                else await repo.list_versions(document_id)
            )
            if version and rows[0] is None:
                raise Missing
            result = [
                {
                    "document_id": r.document_id,
                    "version": r.version,
                    "title": r.title,
                    "body": r.body,
                    "author_id": r.author_id,
                    "created_at": r.created_at,
                }
                for r in rows
                if r
            ]
            return result[0] if version else {"items": result}

    async def link(
        self,
        principal: Principal,
        document_id: UUID,
        target_type: str,
        target_id: UUID,
        remove: bool = False,
    ) -> None:
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            doc = await self._document(repo, document_id, principal, "document.write")
            if remove:
                changed = await repo.remove_link(document_id, target_type, target_id)
            else:
                changed = await repo.add_link(document_id, target_type, target_id)
            if not changed:
                return
            if not await repo.patch_document(
                doc, doc.current_version, principal.user_id, None, None
            ):
                raise Stale
            await session.refresh(doc)
            await self._event(
                session,
                "document.changed",
                doc.project_id,
                principal.user_id,
                {
                    "document_id": str(doc.id),
                    "kind": doc.kind,
                    "title": doc.title,
                    "version": doc.current_version,
                },
                doc.id,
                doc.current_version,
            )

    async def create_upload(self, principal: Principal, data: UploadCreate) -> dict:
        await self._require(principal, data.project_id, "document.write")
        if data.target and data.target.type in {"incident", "review"}:
            await self._require(
                principal,
                data.project_id,
                "incident.manage" if data.target.type == "incident" else "review.write",
            )
        file_id = uuid4()
        key = f"projects/{data.project_id}/{file_id}"
        url, headers, expires = await self.storage.upload_url(key, data.mime)
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            if data.target and data.target.type == "document":
                doc = await repo.get_document(data.target.id)
                if doc is None or doc.project_id != data.project_id:
                    raise Missing
            if not await repo.reserve(
                data.project_id, principal.user_id, data.size, self.project_limit, self.user_limit
            ):
                raise Conflict("quota exceeded")
            session.add(
                File(
                    id=file_id,
                    project_id=data.project_id,
                    bucket=self.storage.bucket,
                    key=key,
                    file_name=data.file_name,
                    mime=data.mime,
                    size=data.size,
                    sha256=data.sha256.lower(),
                    status="pending",
                    uploaded_by=principal.user_id,
                    target_type=data.target.type if data.target else None,
                    target_id=data.target.id if data.target else None,
                )
            )
        return {"id": file_id, "upload_url": url, "headers": headers, "expires_at": expires}

    async def complete_upload(self, principal: Principal, file_id: UUID) -> dict:
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            file = await repo.get_file(file_id, lock=True)
            if file is None:
                raise Missing
            if file.uploaded_by != principal.user_id:
                raise Missing
            await self._require(principal, file.project_id, "document.write")
            if file.status == "pending":
                if not await self.storage.verify(file.key, file.size, file.sha256):
                    raise Conflict("uploaded object size or SHA-256 differs")
                file.status = "ready"
                await repo.release(file, ready=True)
                if file.target_type and file.target_id:
                    if file.target_type == "document":
                        doc = await repo.get_document(file.target_id)
                        if doc is None or doc.project_id != file.project_id:
                            raise Missing
                    await repo.attach(file.id, file.target_type, file.target_id, principal.user_id)
                await self._event(
                    session,
                    "file.ready",
                    file.project_id,
                    principal.user_id,
                    {
                        "file_id": str(file.id),
                        "target_type": file.target_type,
                        "target_id": str(file.target_id) if file.target_id else None,
                    },
                )
            return self._file_json(file)

    @staticmethod
    def _file_json(file: File) -> dict:
        return {
            "id": file.id,
            "project_id": file.project_id,
            "file_name": file.file_name,
            "mime": file.mime,
            "size": file.size,
            "sha256": file.sha256,
            "status": file.status,
            "uploaded_by": file.uploaded_by,
            "created_at": file.created_at,
        }

    async def file_link(self, principal: Principal, file_id: UUID) -> dict:
        async with self.sessions() as session:
            repo = Repository(session)
            file = await repo.get_file(file_id)
            if file is None or file.status != "ready":
                raise Missing
            attachments = await repo.list_attachments(file_id)
            permission = (
                "report.read"
                if any(a.target_type in {"incident", "review"} for a in attachments)
                else "project.read"
            )
            await self._require(principal, file.project_id, permission)
            url, expires = await self.storage.download_url(file.key)
            return {"url": url, "expires_at": expires}

    async def delete_file(self, principal: Principal, file_id: UUID) -> None:
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            file = await repo.get_file(file_id, lock=True)
            if file is None:
                raise Missing
            if file.uploaded_by != principal.user_id:
                await self._require(principal, file.project_id, "document.write")
            attachments = await repo.list_attachments(file_id)
            if any(a.target_type in {"incident", "review"} for a in attachments):
                raise Conflict("incident and review attachments are immutable")
            await self.storage.delete(file.key)
            await repo.delete_file(file)
            await self._event(
                session,
                "file.deleted",
                file.project_id,
                principal.user_id,
                {
                    "file_id": str(file.id),
                    "target_type": file.target_type,
                    "target_id": str(file.target_id) if file.target_id else None,
                },
            )

    async def attachment(
        self,
        principal: Principal,
        target_type: str,
        target_id: UUID,
        file_id: UUID,
        remove: bool = False,
    ) -> None:
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            file = await repo.get_file(file_id)
            if file is None or file.status != "ready":
                raise Missing
            permission = {
                "incident": "incident.manage",
                "review": "review.write",
                "document": "document.write",
            }[target_type]
            await self._require(principal, file.project_id, permission)
            if target_type == "document":
                doc = await repo.get_document(target_id)
                if doc is None or doc.project_id != file.project_id:
                    raise Missing
            if remove:
                if target_type in {"incident", "review"}:
                    raise Conflict("incident and review attachments are immutable")
                await repo.detach(file_id, target_type, target_id)
            else:
                await repo.attach(file_id, target_type, target_id, principal.user_id)

    async def create_export(
        self, principal: Principal, project_id: UUID, data: ExportCreate
    ) -> dict:
        await self._require(principal, project_id, "report.read")
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            if data.kind == "document":
                doc = await repo.get_document(data.target_id)
                if doc is None or doc.project_id != project_id:
                    raise Missing
            item = Export(
                id=uuid4(),
                project_id=project_id,
                requested_by=principal.user_id,
                kind=data.kind,
                target_id=data.target_id,
                status="queued",
            )
            session.add(item)
            await self.outbox.add(
                session,
                MessageV1.command(
                    "pdf.render",
                    "content",
                    {
                        "export_id": str(item.id),
                        "kind": item.kind,
                        "target_id": str(item.target_id),
                    },
                    project_id=project_id,
                    actor=Actor(kind="user", id=str(principal.user_id)),
                ),
                Destination.command("content", "pdf.render"),
            )
            await session.flush()
            return self._export_json(item)

    @staticmethod
    def _export_json(item: Export) -> dict:
        return {
            "id": item.id,
            "kind": item.kind,
            "target_id": item.target_id,
            "status": item.status,
            "file_id": item.file_id,
            "error": item.error,
            "created_at": item.created_at,
            "finished_at": item.finished_at,
        }

    async def get_export(self, principal: Principal, export_id: UUID) -> dict:
        async with self.sessions() as session:
            item = await Repository(session).get_export(export_id)
            if item is None or item.requested_by != principal.user_id:
                raise Missing
            await self._require(principal, item.project_id, "report.read")
            return self._export_json(item)

    async def clean_pending(self) -> int:
        async with self.sessions() as session, session.begin():
            repo = Repository(session)
            files = await repo.pending_expired()
            for file in files:
                await self.storage.delete(file.key)
                await repo.delete_file(file)
            return len(files)

    async def project_deleting(self, message: MessageV1) -> None:
        project_id = UUID(message.data["project_id"])
        async with self.sessions() as session, session.begin():
            if not await self.inbox.accept(session, "content.project_deleting", message):
                return
            files = await Repository(session).delete_project(project_id)
            for file in files:
                try:
                    await self.storage.delete(file.key)
                except BotoCoreError as exc:
                    raise TransientError from exc
            ack = message.caused(
                "cleanup.completed",
                "content",
                {
                    "project_id": str(project_id),
                    "lifecycle_version": message.data["lifecycle_version"],
                    "participant": "content",
                },
                kind="command",
            )
            await self.outbox.add(
                session, ack, Destination.command("projects", "cleanup.completed")
            )
