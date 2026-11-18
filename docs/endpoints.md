# HTTP API

Prefix: `/api/v1`. User Bearer JWT required. Errors: `401` no session,
`403` no permission, `404` absent object/project, `409` immutable attachment
or quota, `412` stale document version, `422` bad input, `503` dependency.

| Method | Path | Request | Response | Permission |
|---|---|---|---|---|
| GET | `/projects/{id}/documents` | `kind`, `target_type`, `target_id`, `cursor` | `DocumentsPage` | `project.read` |
| POST | `/projects/{id}/documents` | `DocumentCreate` | `201 Document` | `document.write` |
| GET | `/documents/{id}` | — | `Document`, `ETag: version` | `project.read` |
| PATCH | `/documents/{id}` | `DocumentPatch`, `If-Match: version` | `Document`; stale `412` | `document.write` |
| DELETE | `/documents/{id}` | — | `204` | `document.write` |
| GET | `/documents/{id}/versions[/{v}]` | — | versions / `DocumentVersion` | `project.read` |
| PUT/DELETE | `/documents/{id}/links/{service\|incident}/{target_id}` | — | `204` | `document.write` |
| POST | `/uploads` | `UploadCreate` | `201 Upload` | `document.write` |
| POST | `/uploads/{id}/complete` | — | `File` | uploader |
| GET | `/files/{id}` | — | `FileLink` | `project.read`; incident/review attachments: `report.read` |
| DELETE | `/files/{id}` | — | `204` | uploader or `document.write` |
| PUT/DELETE | `/attachments/{type}/{target_id}/{file_id}` | — | `204` | document: `document.write`; incident: `incident.manage`; review: `review.write` |
| POST | `/projects/{id}/exports` | `ExportCreate` | `202 Export` | `report.read` |
| GET | `/exports/{id}` | — | `Export` | requester |

`Document` has `id`, `project_id`, `kind`, `title`, `version`, Markdown
`body`, `links`, authors and timestamps. `DocumentPatch` contains optional
`title` and `body` (at least one). Body limit is 1 MiB; title limit 300.
`UploadCreate` has `project_id`, `file_name`, `mime`, `size` (1–25 MiB),
hex `sha256`, optional `{type,id}` target. `Upload` supplies presigned PUT
URL, headers and expiry. `FileLink` supplies a five minute download URL.
`ExportCreate` is `{kind: document|investigation_report, target_id,
format: pdf}`; status is `queued|rendering|ready|failed`.

The public OpenAPI source in `docs/contracts` is outside this repository;
these content schemas need to be merged there before the shared generated
schema can include them.
