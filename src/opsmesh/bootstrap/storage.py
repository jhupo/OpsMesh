"""Select content storage without putting provider credentials in workspace records."""

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.secrets import DeploymentSecretResolver
from opsmesh.infrastructure.storage.api import BlobStore

from .settings import OpsMeshSettings


def create_blob_store(
    settings: OpsMeshSettings, secrets: DeploymentSecretResolver
) -> BlobStore | None:
    if settings.storage_backend == "local":
        return None
    if not settings.s3_bucket:
        raise OpsMeshError("CONFIG_INVALID", "S3 storage requires a bucket")
    from opsmesh.infrastructure.storage.s3 import S3BlobStore

    return S3BlobStore(
        settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region_name,
        prefix=settings.s3_prefix,
        access_key_id=secrets.resolve(settings.s3_access_key_secret_ref)
        if settings.s3_access_key_secret_ref
        else None,
        secret_access_key=secrets.resolve(settings.s3_secret_key_secret_ref)
        if settings.s3_secret_key_secret_ref
        else None,
        session_token=secrets.resolve(settings.s3_session_token_secret_ref)
        if settings.s3_session_token_secret_ref
        else None,
    )
