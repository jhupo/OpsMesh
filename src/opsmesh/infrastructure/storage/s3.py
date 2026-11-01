"""S3-compatible conditional writes, bounded reads and verified content addressing."""

import asyncio
import base64
import hashlib
import importlib
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from opsmesh.framework.errors import OpsMeshError

from .api import BlobInfo, BlobPage, validate_digest, validate_page_limit


def _storage_error(error: Exception) -> OpsMeshError:
    response = getattr(error, "response", {})
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = response.get("Error", {}).get("Code")
    if status == 404 or code in {"NoSuchKey", "NotFound", "404"}:
        return OpsMeshError("NOT_FOUND", "Blob not found", provider="s3")
    if status in {409, 412}:
        return OpsMeshError("CONFLICT", "Blob conditional operation conflicted", provider="s3")
    if status in {401, 403}:
        return OpsMeshError("STORAGE_ACCESS_DENIED", "Blob storage access denied", provider="s3")
    if status in {400, 405, 501}:
        return OpsMeshError(
            "STORAGE_PROTOCOL_ERROR", "S3 server rejected the required operation", provider="s3"
        )
    return OpsMeshError(
        "STORAGE_UNAVAILABLE", "Blob storage request failed", retryable=True, provider="s3"
    )


class S3BlobStore:
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        prefix: str = "opsmesh/objects",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        max_blob_bytes: int = 64 << 20,
        request_timeout_seconds: float = 30,
        client: Any = None,
    ) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise OpsMeshError("CONFIG_INVALID", "Invalid S3 bucket name")
        if max_blob_bytes <= 0 or not (
            math.isfinite(request_timeout_seconds) and request_timeout_seconds > 0
        ):
            raise OpsMeshError("CONFIG_INVALID", "Storage limits must be positive and finite")
        if prefix and (
            not re.fullmatch(r"[A-Za-z0-9._/-]+", prefix)
            or any(part in {"", ".", ".."} for part in prefix.split("/"))
        ):
            raise OpsMeshError("CONFIG_INVALID", "Invalid S3 object prefix")
        if endpoint_url is not None:
            parsed = urlsplit(endpoint_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise OpsMeshError("CONFIG_INVALID", "S3 endpoint must be a credential-free origin")
        if bool(access_key_id) != bool(secret_access_key):
            raise OpsMeshError("CONFIG_INVALID", "S3 credentials require both key and secret")
        self.bucket, self.prefix = bucket, f"{prefix}/" if prefix else ""
        self._limit = max_blob_bytes
        self._identity = f"s3:{(endpoint_url or 'aws').rstrip('/')}:{bucket}:{self.prefix}"
        self._client = client or self._create_client(
            endpoint_url,
            region_name,
            access_key_id,
            secret_access_key,
            session_token,
            request_timeout_seconds,
        )

    @staticmethod
    def _create_client(
        endpoint: str | None,
        region: str | None,
        key: str | None,
        secret: str | None,
        token: str | None,
        timeout: float,
    ) -> Any:
        try:
            boto3 = importlib.import_module("boto3")
            config_type = importlib.import_module("botocore.config").Config
        except ImportError as error:
            raise OpsMeshError(
                "DEPENDENCY_UNAVAILABLE", "Install opsmesh[s3] for S3 storage"
            ) from error
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            aws_session_token=token,
            config=config_type(
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"total_max_attempts": 3, "mode": "standard"},
                s3={"addressing_style": "path"},
                signature_version="s3v4",
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    @property
    def identity(self) -> str:
        return self._identity

    def _key(self, digest: str) -> str:
        return self.prefix + validate_digest(digest)

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(getattr(self._client, method), **kwargs)
        except Exception as error:
            raise _storage_error(error) from error

    async def put(self, data: bytes) -> str:
        if len(data) > self._limit:
            raise OpsMeshError("RESOURCE_EXHAUSTED", "Blob exceeds the configured size limit")
        digest = hashlib.sha256(data).hexdigest()
        for attempt in range(3):
            try:
                await self._call(
                    "put_object",
                    Bucket=self.bucket,
                    Key=self._key(digest),
                    Body=data,
                    IfNoneMatch="*",
                    ContentType="application/octet-stream",
                    Metadata={"sha256": digest},
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
                )
                return digest
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
                try:
                    await self.get(digest)
                    return digest
                except OpsMeshError as missing:
                    if missing.code != "NOT_FOUND":
                        raise
                if attempt == 2:
                    raise OpsMeshError(
                        "STORAGE_BUSY", "Concurrent blob creation needs retry", retryable=True
                    ) from error
                await asyncio.sleep(0.02 * (attempt + 1))
        raise AssertionError("Bounded retry exited unexpectedly")

    async def get(self, digest: str) -> bytes:
        key = self._key(digest)

        def read() -> bytes:
            try:
                response = self._client.get_object(Bucket=self.bucket, Key=key)
                body = response["Body"]
                try:
                    if response["ContentLength"] > self._limit:
                        raise OpsMeshError("CONTENT_CORRUPT", "Blob exceeds configured size limit")
                    data: bytes = body.read(self._limit + 1)
                finally:
                    body.close()
            except OpsMeshError:
                raise
            except Exception as error:
                raise _storage_error(error) from error
            if len(data) > self._limit or hashlib.sha256(data).hexdigest() != digest:
                raise OpsMeshError("CONTENT_CORRUPT", "Blob size or content digest mismatch")
            return data

        return await asyncio.to_thread(read)

    @staticmethod
    def _info(digest: str, response: dict[str, Any], size_key: str) -> BlobInfo:
        version = response.get("ETag")
        modified = response.get("LastModified")
        if not version or not isinstance(modified, datetime):
            raise OpsMeshError("STORAGE_PROTOCOL_ERROR", "S3 object metadata is incomplete")
        return BlobInfo(
            digest=digest,
            size=response[size_key],
            version=version,
            modified_at=modified.astimezone(UTC),
        )

    async def stat(self, digest: str) -> BlobInfo:
        response = await self._call("head_object", Bucket=self.bucket, Key=self._key(digest))
        return self._info(digest, response, "ContentLength")

    async def is_empty(self) -> bool:
        response = await self._call(
            "list_objects_v2", Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1
        )
        return not bool(response.get("Contents") or response.get("IsTruncated"))

    async def list(self, *, continuation: str | None = None, limit: int = 1000) -> BlobPage:
        validate_page_limit(limit)
        parameters = {"ContinuationToken": continuation} if continuation is not None else {}
        response = await self._call(
            "list_objects_v2", Bucket=self.bucket, Prefix=self.prefix, MaxKeys=limit, **parameters
        )
        items: list[BlobInfo] = []
        for value in response.get("Contents", []):
            key = value["Key"]
            digest = key[len(self.prefix) :]
            if key.startswith(self.prefix) and re.fullmatch(r"[a-f0-9]{64}", digest):
                items.append(self._info(digest, value, "Size"))
        token = response.get("NextContinuationToken")
        if response.get("IsTruncated") and not token:
            raise OpsMeshError("STORAGE_PROTOCOL_ERROR", "S3 listing lacks continuation token")
        return BlobPage(items=tuple(items), continuation=token)

    async def delete(self, digest: str, *, expected_version: str) -> bool:
        try:
            info = await self.stat(digest)
        except OpsMeshError as error:
            if error.code == "NOT_FOUND":
                return False
            raise
        if info.version != expected_version:
            raise OpsMeshError("CONFLICT", "Blob changed since inspection")
        await self._call(
            "delete_object", Bucket=self.bucket, Key=self._key(digest), IfMatch=expected_version
        )
        return True

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)
