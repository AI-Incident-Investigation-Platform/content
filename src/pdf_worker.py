import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime
from html import escape
from uuid import UUID

import aio_pika
import httpx
from aiip_kit.errors import TransientError
from aiip_kit.messaging.consumer import Binding, Consumer, QueueSpec
from aiip_kit.messaging.message import MessageV1
from aiip_kit.messaging.topology import Destination, commands_exchange
from aiip_kit.outbox.inbox import InboxGuard
from aiip_kit.outbox.writer import OutboxWriter
from aiip_kit.service_auth.provider import AuthServiceTokenProvider
from botocore.exceptions import BotoCoreError
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.db.models import File, inbox, outbox
from src.core.db.repository import Repository
from src.core.storage import ObjectStorage


def render_pdf(title: str, body: str, max_pages: int = 100) -> bytes:
    output = io.BytesIO()
    styles = getSampleStyleSheet()
    story = [Paragraph(escape(title), styles["Title"]), Spacer(1, 12)]
    for line in body.splitlines() or [""]:
        story.append(Paragraph(escape(line) or "&nbsp;", styles["BodyText"]))
        story.append(Spacer(1, 5))

    def page_limit(canvas, document) -> None:
        if document.page > max_pages:
            raise ValueError("PDF page limit exceeded")

    SimpleDocTemplate(output, pagesize=(595, 842)).build(
        story, onFirstPage=page_limit, onLaterPages=page_limit
    )
    return output.getvalue()


def format_report_payload(payload: object, project_id: UUID) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("invalid investigation report response")
    report = payload.get("report")
    incident = payload.get("incident")
    if not isinstance(report, dict) or not isinstance(incident, dict):
        raise ValueError("invalid investigation report response")
    if incident.get("project_id") != str(project_id):
        raise ValueError("investigation report belongs to a different project")
    incident_title = incident.get("title")
    title = (
        incident_title
        if isinstance(incident_title, str) and incident_title
        else "Investigation report"
    )
    lines = [f"Incident: {title}"]
    for key in ("severity", "status"):
        value = incident.get(key)
        if isinstance(value, str) and value:
            lines.append(f"{key.capitalize()}: {value}")
    lines.append("")
    for key, value in report.items():
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    return title, "\n".join(lines)


class PdfWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        http: httpx.AsyncClient,
        tokens: AuthServiceTokenProvider,
        incidents_url: str,
        timeout: float = 30,
        max_pages: int = 100,
        project_limit: int = 1073741824,
        user_limit: int = 268435456,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.http = http
        self.tokens = tokens
        self.incidents_url = incidents_url.rstrip("/")
        self.timeout = timeout
        self.max_pages = max_pages
        self.project_limit = project_limit
        self.user_limit = user_limit
        self.guard = InboxGuard(inbox)
        self.writer = OutboxWriter(outbox)

    async def handle(self, message: MessageV1, attempt: int) -> None:
        export_id = UUID(message.data["export_id"])
        try:
            async with self.sessions() as session, session.begin():
                item = await Repository(session).get_export(export_id, lock=True)
                if item is None or item.status in {"ready", "failed"}:
                    return
                item.status = "rendering"
                kind, target_id, project_id, actor = (
                    item.kind,
                    item.target_id,
                    item.project_id,
                    item.requested_by,
                )
                if kind == "document":
                    repo = Repository(session)
                    doc = await repo.get_document(target_id)
                    if doc is None or doc.project_id != project_id:
                        raise ValueError("document missing")
                    version = await repo.get_version(doc.id, doc.current_version)
                    if version is None:
                        raise ValueError("document version missing")
                    title, body = doc.title, version.body
                else:
                    title, body = "Investigation report", ""
            if kind == "investigation_report":
                token = await self.tokens.token("incidents", ["incidents.reports.read"])
                response = await self.http.get(
                    f"{self.incidents_url}/internal/v1/investigations/{target_id}/report",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                title, body = format_report_payload(response.json(), project_id)
                if len(body.encode()) > 1048576:
                    raise ValueError("report exceeds PDF input limit")
            data = await asyncio.wait_for(
                asyncio.to_thread(render_pdf, title, body, self.max_pages), timeout=self.timeout
            )
            if len(data) > 26214400:
                raise ValueError("PDF exceeds file size limit")
            file_id = export_id
            key = f"projects/{project_id}/exports/{file_id}.pdf"
            await self.storage.put(key, data, "application/pdf")
            async with self.sessions() as session, session.begin():
                if not await self.guard.accept(session, "content.pdf_render", message):
                    return
                repo = Repository(session)
                item = await repo.get_export(export_id, lock=True)
                if item is None or item.status == "ready":
                    return
                if not await repo.reserve(
                    project_id, actor, len(data), self.project_limit, self.user_limit
                ):
                    raise ValueError("quota exceeded")
                file = File(
                    id=file_id,
                    project_id=project_id,
                    bucket=self.storage.bucket,
                    key=key,
                    file_name=f"{kind}-{target_id}.pdf",
                    mime="application/pdf",
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    status="ready",
                    uploaded_by=actor,
                )
                session.add(file)
                await repo.release(file, ready=True)
                item.status, item.file_id, item.finished_at = "ready", file_id, datetime.now(UTC)
                await self._finished(session, message, item, file_id, None)
        except (httpx.ConnectError, httpx.TimeoutException, BotoCoreError, SQLAlchemyError) as exc:
            raise TransientError from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise TransientError from exc
            await self._fail(message, export_id, exc)
        except Exception as exc:
            await self._fail(message, export_id, exc)

    async def _fail(self, message: MessageV1, export_id: UUID, exc: Exception) -> None:
        async with self.sessions() as session, session.begin():
            if not await self.guard.accept(session, "content.pdf_render", message):
                return
            item = await Repository(session).get_export(export_id, lock=True)
            if item is not None and item.status not in {"ready", "failed"}:
                await self.storage.delete(f"projects/{item.project_id}/exports/{export_id}.pdf")
                item.status, item.error, item.finished_at = (
                    "failed",
                    str(exc)[:500],
                    datetime.now(UTC),
                )
                await self._finished(session, message, item, None, item.error)

    async def _finished(
        self,
        session: AsyncSession,
        source: MessageV1,
        item,
        file_id: UUID | None,
        error: str | None,
    ) -> None:
        kind = "export.completed" if file_id else "export.failed"
        data = {
            "export_id": str(item.id),
            "requested_by": str(item.requested_by),
            "file_id": str(file_id) if file_id else None,
            "error": error,
        }
        await self.writer.add(
            session, source.caused(kind, "content", data), Destination.event("content", kind)
        )
        await self.writer.add(
            session,
            source.caused(kind, "content", data),
            Destination.live("content", f"user.{item.requested_by}"),
        )


def pdf_queue_spec() -> QueueSpec:
    return QueueSpec(
        name="content.pdf-render",
        bindings=[Binding(commands_exchange("content"), "direct", "pdf.render")],
        prefetch=1,
        max_length=100,
    )


async def run_pdf_worker(worker: PdfWorker, rabbitmq_url: str) -> None:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    try:
        channel = await connection.channel(publisher_confirms=True)
        await Consumer(channel, pdf_queue_spec(), worker.handle).start()
        await asyncio.Future()
    finally:
        await connection.close()
