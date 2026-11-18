from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

DocumentKind = Literal["runbook", "postmortem", "note"]
TargetType = Literal["service", "incident"]


class DocumentLinkIn(BaseModel):
    target_type: TargetType
    target_id: UUID


class DocumentCreate(BaseModel):
    kind: DocumentKind
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(max_length=1048576)
    links: list[DocumentLinkIn] = Field(default_factory=list)


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, max_length=1048576)

    @model_validator(mode="after")
    def nonempty(self) -> DocumentPatch:
        if self.title is None and self.body is None:
            raise ValueError("title or body required")
        return self


class UploadTarget(BaseModel):
    type: Literal["incident", "review", "document"]
    id: UUID


class UploadCreate(BaseModel):
    project_id: UUID
    file_name: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=128)
    size: int = Field(gt=0, le=26214400)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    target: UploadTarget | None = None

    @field_validator("mime")
    @classmethod
    def allowed_mime(cls, value: str) -> str:
        if value.startswith("image/") or value in {
            "application/pdf",
            "text/plain",
            "text/markdown",
        }:
            return value
        raise ValueError("unsupported mime")


class ExportCreate(BaseModel):
    kind: Literal["investigation_report", "document"]
    target_id: UUID
    format: Literal["pdf"]
