import os
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol

from backend.app.files.security import validate_storage_key

if TYPE_CHECKING:
    from backend.app.core.config import Settings


class ObjectStorage(Protocol):
    def put(self, storage_key: str, content: bytes) -> None: ...

    def get(self, storage_key: str) -> bytes: ...

    def delete(self, storage_key: str) -> None: ...

    def exists(self, storage_key: str) -> bool: ...

    def open(self, storage_key: str) -> BinaryIO: ...

    def write(self, storage_key: str, content: bytes) -> None: ...

    def read(self, storage_key: str) -> bytes: ...

    def read_limited(self, storage_key: str, max_bytes: int) -> bytes: ...


class StorageObjectTooLargeError(ValueError):
    pass


class LocalStorage:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def put(self, storage_key: str, content: bytes) -> None:
        self.write(storage_key, content)

    def get(self, storage_key: str) -> bytes:
        return self.read(storage_key)

    def delete(self, storage_key: str) -> None:
        _io_path(self._path_for_key(storage_key)).unlink(missing_ok=True)

    def open(self, storage_key: str) -> BinaryIO:
        return _io_path(self._path_for_key(storage_key)).open("rb")

    def write(self, storage_key: str, content: bytes) -> None:
        path = _io_path(self._path_for_key(storage_key))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def read(self, storage_key: str) -> bytes:
        return _io_path(self._path_for_key(storage_key)).read_bytes()

    def read_limited(self, storage_key: str, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise ValueError("Object read limit must not be negative")
        with self.open(storage_key) as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise StorageObjectTooLargeError("Storage object exceeds the read limit")
        return content

    def exists(self, storage_key: str) -> bool:
        return _io_path(self._path_for_key(storage_key)).exists()

    def _path_for_key(self, storage_key: str) -> Path:
        path = (self._root / validate_storage_key(storage_key)).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("Storage key escapes storage root")
        return path


def _io_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    path_text = str(path)
    if path_text.startswith("\\\\?\\"):
        return path
    return Path(f"\\\\?\\{path_text}")


class S3Storage:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        client: Any | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        use_ssl: bool = True,
        addressing_style: str = "auto",
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 storage bucket is required")
        self._bucket = bucket.strip()
        self._prefix = _normalize_prefix(prefix)
        self._client = client or _create_s3_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
            use_ssl=use_ssl,
            addressing_style=addressing_style,
        )

    def put(self, storage_key: str, content: bytes) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._object_key(storage_key),
            Body=content,
        )

    def get(self, storage_key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=self._object_key(storage_key))
        body = response["Body"]
        try:
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()

    def delete(self, storage_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._object_key(storage_key))

    def exists(self, storage_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._object_key(storage_key))
        except Exception as exc:
            if _is_not_found_error(exc):
                return False
            raise
        return True

    def open(self, storage_key: str) -> BinaryIO:
        return BytesIO(self.get(storage_key))

    def write(self, storage_key: str, content: bytes) -> None:
        self.put(storage_key, content)

    def read(self, storage_key: str) -> bytes:
        return self.get(storage_key)

    def read_limited(self, storage_key: str, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise ValueError("Object read limit must not be negative")
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=self._object_key(storage_key),
                Range=f"bytes=0-{max_bytes}",
            )
        except Exception as exc:
            if _is_not_found_error(exc):
                raise FileNotFoundError("Storage object not found") from exc
            raise
        body = response["Body"]
        try:
            content = body.read(max_bytes + 1)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()
        if len(content) > max_bytes:
            raise StorageObjectTooLargeError("Storage object exceeds the read limit")
        return content

    def _object_key(self, storage_key: str) -> str:
        normalized = validate_storage_key(storage_key)
        if not self._prefix:
            return normalized
        return f"{self._prefix}/{normalized}"


def create_storage(settings: "Settings") -> ObjectStorage:
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_root)
    if settings.storage_backend == "s3":
        return S3Storage(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            session_token=settings.s3_session_token,
            use_ssl=settings.s3_use_ssl,
            addressing_style=settings.s3_addressing_style,
        )
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")


def _create_s3_client(
    *,
    endpoint_url: str | None,
    region_name: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    session_token: str | None,
    use_ssl: bool,
    addressing_style: str,
) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        aws_session_token=session_token,
        use_ssl=use_ssl,
        config=Config(s3={"addressing_style": addressing_style}),
    )


def _normalize_prefix(prefix: str) -> str:
    stripped = prefix.strip().strip("/")
    if not stripped:
        return ""
    return validate_storage_key(stripped)


def _is_not_found_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    if not isinstance(error, dict):
        return False
    return str(error.get("Code")) in {"404", "NoSuchKey", "NotFound"}
