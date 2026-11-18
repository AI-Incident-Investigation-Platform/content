from aiip_kit.outbox.tables import inbox_table, outbox_table

from src.core.db.models.base import Base
from src.core.db.models.content import (
    Attachment,
    AuditEvent,
    Document,
    DocumentLink,
    DocumentVersion,
    Export,
    File,
    IdempotencyKey,
    Quota,
)

inbox = inbox_table(Base.metadata)
outbox = outbox_table(Base.metadata)

__all__ = [
    "Attachment",
    "AuditEvent",
    "Base",
    "Document",
    "DocumentLink",
    "DocumentVersion",
    "Export",
    "File",
    "IdempotencyKey",
    "Quota",
    "inbox",
    "outbox",
]
