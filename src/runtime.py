import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import aio_pika
import httpx
import uvicorn
from aiip_kit.access.checker import DragonflyAccessChecker
from aiip_kit.access.source import HttpAclSource
from aiip_kit.messaging.consumer import Binding, Consumer, QueueSpec, declare_topology
from aiip_kit.messaging.publisher import AmqpPublisher
from aiip_kit.messaging.topology import events_exchange
from aiip_kit.outbox.relay import OutboxRelay
from aiip_kit.service_auth.jwks import JwksKeys
from aiip_kit.service_auth.provider import AuthServiceTokenProvider
from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.service import ContentService
from src.core.db.models import outbox
from src.core.observability import setup_logging, setup_observability
from src.core.storage import ObjectStorage
from src.core.user_auth import JwtUserAuthenticator
from src.main import create_app
from src.pdf_worker import PdfWorker, pdf_queue_spec, run_pdf_worker


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = Field(min_length=1)
    rabbitmq_url: str = Field(min_length=1)
    cache_url: str = Field(min_length=1)
    s3_endpoint: str = Field(min_length=1)
    s3_access_key: str = Field(min_length=1)
    s3_secret_key: str = Field(min_length=1)
    s3_bucket: str = "content"
    s3_public_endpoint: str | None = None
    auth_url: str = "http://auth.apps:8001"
    projects_url: str = "http://projects.apps:8003"
    incidents_url: str = "http://incidents.apps:8006"
    auth_client_id: str = "content"
    auth_client_secret: str = Field(min_length=1)
    project_quota_bytes: int = 1073741824
    user_quota_bytes: int = 268435456
    port: int = 8008
    bind_host: str = "0.0.0.0"
    otlp_endpoint: str | None = None


cfg = Config()  # type: ignore[call-arg]
pdf_mode = os.environ.get("CONTENT_ROLE") == "pdf"
engine = create_async_engine(
    cfg.database_url, pool_size=1 if pdf_mode else 3, max_overflow=0, pool_timeout=0.5
)
sessions = async_sessionmaker(engine, expire_on_commit=False)
http = httpx.AsyncClient(timeout=5)
redis = Redis.from_url(cfg.cache_url, decode_responses=True)
keys = JwksKeys(http, f"{cfg.auth_url.rstrip('/')}/.well-known/jwks.json")
authenticator = JwtUserAuthenticator(keys)
tokens = AuthServiceTokenProvider(http, cfg.auth_url, cfg.auth_client_id, cfg.auth_client_secret)
source = HttpAclSource(
    http, cfg.projects_url, lambda: tokens.token("projects", ["projects.acl.read"])
)
access = DragonflyAccessChecker(redis, source)
storage = ObjectStorage(
    cfg.s3_endpoint, cfg.s3_access_key, cfg.s3_secret_key, cfg.s3_bucket, cfg.s3_public_endpoint
)
publisher = AmqpPublisher(cfg.rabbitmq_url)
relay = OutboxRelay(sessions, outbox, publisher)
stop = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    relay_task = asyncio.create_task(relay.run(stop))
    cleanup_task = asyncio.create_task(cleanup_loop())
    connection = await aio_pika.connect_robust(cfg.rabbitmq_url)
    channel = await connection.channel(publisher_confirms=True)
    await declare_topology(channel, pdf_queue_spec())
    await Consumer(
        channel,
        QueueSpec(
            name="content.projects-events",
            bindings=[Binding(events_exchange("projects"), "topic", "project.deleting")],
        ),
        lambda message, attempt: service.project_deleting(message),
    ).start()
    try:
        yield
    finally:
        stop.set()
        await relay_task
        await cleanup_task
        await publisher.close()
        await http.aclose()
        await redis.aclose()
        await connection.close()
        await engine.dispose()


service = ContentService(sessions, access, storage, cfg.project_quota_bytes, cfg.user_quota_bytes)
app = create_app(service, authenticator.authenticate)
setup_observability(app, engine, cfg.otlp_endpoint)
app.router.lifespan_context = lifespan


async def cleanup_loop() -> None:
    while not stop.is_set():
        await service.clean_pending()
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=3600)


@app.get("/ready")
async def ready() -> dict[str, str]:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    await redis.ping()
    return {"status": "ok"}


def main() -> None:
    if pdf_mode:
        setup_logging()
        worker = PdfWorker(
            sessions,
            storage,
            http,
            tokens,
            cfg.incidents_url,
            project_limit=cfg.project_quota_bytes,
            user_limit=cfg.user_quota_bytes,
        )
        asyncio.run(run_pdf_worker(worker, cfg.rabbitmq_url))
    else:
        uvicorn.run("src.runtime:app", host=cfg.bind_host, port=cfg.port, workers=1)


if __name__ == "__main__":
    main()
