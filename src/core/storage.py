import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import boto3
from botocore.client import Config


class ObjectStorage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        public_endpoint: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self.public_client = boto3.client(
            "s3",
            endpoint_url=public_endpoint or endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def upload_url(self, key: str, mime: str) -> tuple[str, dict[str, str], datetime]:
        url = await asyncio.to_thread(
            self.public_client.generate_presigned_url,
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": mime},
            ExpiresIn=900,
        )
        return url, {"Content-Type": mime}, datetime.now(UTC) + timedelta(minutes=15)

    async def download_url(self, key: str) -> tuple[str, datetime]:
        url = await asyncio.to_thread(
            self.public_client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=300,
        )
        return url, datetime.now(UTC) + timedelta(minutes=5)

    async def verify(self, key: str, size: int, sha256: str) -> bool:
        def check() -> bool:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
            if obj["ContentLength"] != size:
                obj["Body"].close()
                return False
            digest = hashlib.sha256()
            count = 0
            try:
                for chunk in obj["Body"].iter_chunks(chunk_size=65536):
                    count += len(chunk)
                    if count > size:
                        return False
                    digest.update(chunk)
            finally:
                obj["Body"].close()
            return count == size and digest.hexdigest() == sha256.lower()

        return await asyncio.to_thread(check)

    async def put(self, key: str, data: bytes, mime: str) -> None:
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=mime
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)
