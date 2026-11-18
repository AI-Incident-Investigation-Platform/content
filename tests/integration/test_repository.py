import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.db.models import Base
from src.core.db.repository import Repository


@pytest.mark.asyncio
async def test_document_version_and_quota_in_postgres() -> None:
    dsn = os.environ.get("CONTENT_TEST_DATABASE_URL")
    if dsn is None:
        pytest.skip("set CONTENT_TEST_DATABASE_URL to a dedicated content_test database")
    if not dsn.split("?")[0].endswith("/content_test"):
        pytest.fail("CONTENT_TEST_DATABASE_URL must name content_test")

    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    repo = Repository(session)
                    project, actor = uuid4(), uuid4()
                    doc = await repo.create_document(project, actor, "note", "first", "body", [])
                    assert await repo.patch_document(doc, 1, actor, "second", None)
                    assert not await repo.patch_document(doc, 1, actor, "stale", None)
                    version = await repo.get_version(doc.id, 2)
                    assert version is not None and version.body == "body"
                    assert await repo.reserve(project, actor, 10, 10, 10)
                    assert not await repo.reserve(project, actor, 1, 10, 10)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
