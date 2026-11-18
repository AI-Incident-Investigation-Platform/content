from uuid import uuid4

import httpx
import pytest
from aiip_kit.access.model import Principal

from src.main import create_app


@pytest.mark.asyncio
async def test_patch_requires_if_match_before_mutation() -> None:
    async def authenticate(_: str | None) -> Principal:
        return Principal(user_id=uuid4(), session_id=uuid4())

    app = create_app(None, authenticate)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(f"/api/v1/documents/{uuid4()}", json={"title": "new"})
    assert response.status_code == 428
