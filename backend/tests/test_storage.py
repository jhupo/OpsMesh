from io import BytesIO
from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.files import storage as storage_module
from backend.app.files.storage import (
    LocalStorage,
    S3Storage,
    StorageObjectTooLargeError,
    create_storage,
)


def test_local_storage_supports_object_storage_semantics(tmp_path: Path) -> None:
    storage = LocalStorage(str(tmp_path / "storage"))

    storage.put("workspaces/demo/file.txt", b"hello")

    assert storage.exists("workspaces/demo/file.txt") is True
    assert storage.get("workspaces/demo/file.txt") == b"hello"
    with storage.open("workspaces/demo/file.txt") as stream:
        assert stream.read() == b"hello"
    assert storage.read_limited("workspaces/demo/file.txt", 5) == b"hello"
    with pytest.raises(StorageObjectTooLargeError):
        storage.read_limited("workspaces/demo/file.txt", 4)

    storage.delete("workspaces/demo/file.txt")

    assert storage.exists("workspaces/demo/file.txt") is False


def test_s3_storage_supports_object_storage_semantics() -> None:
    client = FakeS3Client()
    storage = S3Storage(bucket="opsmesh", prefix="tenant-a", client=client)

    storage.put("workspaces/demo/file.txt", b"hello")

    assert client.objects[("opsmesh", "tenant-a/workspaces/demo/file.txt")] == b"hello"
    assert storage.exists("workspaces/demo/file.txt") is True
    assert storage.get("workspaces/demo/file.txt") == b"hello"
    with storage.open("workspaces/demo/file.txt") as stream:
        assert stream.read() == b"hello"
    assert storage.read_limited("workspaces/demo/file.txt", 5) == b"hello"
    with pytest.raises(StorageObjectTooLargeError):
        storage.read_limited("workspaces/demo/file.txt", 4)

    storage.delete("workspaces/demo/file.txt")

    assert storage.exists("workspaces/demo/file.txt") is False


def test_s3_storage_rejects_unsafe_keys_and_prefixes() -> None:
    storage = S3Storage(bucket="opsmesh", client=FakeS3Client())

    with pytest.raises(ValueError, match="Storage key"):
        storage.put("../escape.txt", b"bad")

    with pytest.raises(ValueError, match="Storage key"):
        S3Storage(bucket="opsmesh", prefix="../tenant", client=FakeS3Client())


def test_create_storage_selects_local_backend(tmp_path: Path) -> None:
    settings = Settings(environment="test", storage_root=str(tmp_path))

    storage = create_storage(settings)

    assert isinstance(storage, LocalStorage)


def test_create_storage_selects_s3_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class StubS3Storage:
        def __init__(self, **kwargs: object) -> None:
            created.update(kwargs)

    monkeypatch.setattr(storage_module, "S3Storage", StubS3Storage)
    settings = Settings(
        environment="test",
        storage_backend="s3",
        s3_bucket="opsmesh",
        s3_endpoint_url="http://minio:9000",
        s3_region="us-east-1",
        s3_prefix="dev",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
        s3_session_token="token",
        s3_use_ssl=False,
        s3_addressing_style="path",
    )

    storage = create_storage(settings)

    assert isinstance(storage, StubS3Storage)
    assert created == {
        "bucket": "opsmesh",
        "prefix": "dev",
        "endpoint_url": "http://minio:9000",
        "region_name": "us-east-1",
        "access_key_id": "access",
        "secret_access_key": "secret",
        "session_token": "token",
        "use_ssl": False,
        "addressing_style": "path",
    }


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Range: str | None = None,
    ) -> dict[str, BytesIO]:
        try:
            content = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise FakeS3NotFound() from exc
        if Range is not None:
            end = int(Range.removeprefix("bytes=0-"))
            content = content[: end + 1]
        return {"Body": BytesIO(content)}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)

    def head_object(self, *, Bucket: str, Key: str) -> None:
        if (Bucket, Key) not in self.objects:
            raise FakeS3NotFound()


class FakeS3NotFound(Exception):
    response = {"Error": {"Code": "404"}}
