from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db.models.base import Base


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    current_version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[UUID]
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("kind IN ('runbook','postmortem','note')", name="kind"),
        CheckConstraint("current_version > 0", name="version"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DocumentLink(Base):
    __tablename__ = "document_links"
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    target_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[UUID] = mapped_column(primary_key=True)
    __table_args__ = (CheckConstraint("target_type IN ('service','incident')", name="target_type"),)


class File(Base):
    __tablename__ = "files"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(index=True)
    bucket: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(512), unique=True)
    file_name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    uploaded_by: Mapped[UUID]
    target_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    __table_args__ = (
        CheckConstraint("size > 0 AND size <= 26214400", name="size"),
        CheckConstraint("status IN ('pending','ready')", name="status"),
    )


class Attachment(Base):
    __tablename__ = "attachments"
    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    target_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[UUID] = mapped_column(primary_key=True)
    attached_by: Mapped[UUID]
    attached_at: Mapped[datetime] = mapped_column(server_default=func.now())
    __table_args__ = (
        CheckConstraint("target_type IN ('incident','review','document')", name="target_type"),
    )


class Quota(Base):
    __tablename__ = "quotas"
    project_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(8), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(primary_key=True)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        CheckConstraint("used_bytes >= 0 AND reserved_bytes >= 0", name="bytes"),
        CheckConstraint("scope IN ('project','user')", name="scope"),
    )


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(index=True)
    requested_by: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(16), default="queued")
    file_id: Mapped[UUID | None] = mapped_column(ForeignKey("files.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint("kind IN ('document','investigation_report')", name="kind"),
        CheckConstraint("status IN ('queued','rendering','ready','failed')", name="status"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    project_id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(index=True)
    actor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[UUID]
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
