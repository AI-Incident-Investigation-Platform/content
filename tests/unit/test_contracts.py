from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas import DocumentCreate, DocumentPatch, UploadCreate
from src.pdf_worker import format_report_payload, render_pdf


def test_document_body_is_limited_to_one_mib() -> None:
    with pytest.raises(ValidationError):
        DocumentCreate(kind="note", title="test", body="x" * (1048576 + 1))


def test_empty_document_patch_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DocumentPatch()


def test_upload_rejects_excess_size_and_unsupported_mime() -> None:
    project_id = uuid4()
    with pytest.raises(ValidationError):
        UploadCreate(
            project_id=project_id,
            file_name="x",
            sha256="a" * 64,
            size=26214401,
            mime="application/pdf",
        )
    with pytest.raises(ValidationError):
        UploadCreate(
            project_id=project_id,
            file_name="x",
            sha256="a" * 64,
            size=1,
            mime="application/x-executable",
        )


def test_pdf_render_is_real_pdf_and_enforces_page_limit() -> None:
    assert render_pdf("Title", "hello").startswith(b"%PDF-")
    with pytest.raises(ValueError, match="page limit"):
        render_pdf("Title", "\n".join("line" for _ in range(300)), max_pages=1)


def test_incidents_internal_report_payload_is_unwrapped_for_pdf() -> None:
    project_id = uuid4()
    payload = {
        "incident": {
            "id": str(uuid4()),
            "project_id": str(project_id),
            "title": "Database outage",
            "severity": "critical",
            "status": "resolved",
        },
        "report": {
            "summary": "Original RCA",
            "root_cause": "DB pool exhausted",
            "remediation": ["Increase pool capacity"],
        },
    }
    title, body = format_report_payload(payload, project_id)
    assert title == "Database outage"
    assert "summary: Original RCA" in body
    assert "root_cause: DB pool exhausted" in body
    assert "Increase pool capacity" in body
    assert "project_id:" not in body
    with pytest.raises(ValueError, match="different project"):
        format_report_payload(payload, uuid4())
