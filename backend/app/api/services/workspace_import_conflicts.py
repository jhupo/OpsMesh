from backend.app.api.schemas.exports import (
    WorkspaceExportResponse,
    WorkspaceImportConflict,
)
from backend.app.api.services.workspace_export_constants import (
    SUPPORTED_WORKSPACE_EXPORT_FORMAT,
)


def _skip_conflict(
    *,
    collection: str,
    source_id: str,
    field: str,
    source_value: str,
    target_value: str,
    message: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field=field,
        source_value=source_value,
        target_value=target_value,
        strategy="skip_existing",
        severity="warning",
        message=message,
    )


def _missing_dependency_conflict(
    *,
    collection: str,
    source_id: str,
    dependency: str,
    dependency_id: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field=f"{dependency}_id",
        source_value=dependency_id,
        strategy="skip_missing_dependency",
        severity="warning",
        message=(
            f"Skipped {collection[:-1].replace('_', ' ')} because imported "
            f"{dependency.replace('_', ' ')} {dependency_id!r} is unavailable."
        ),
    )


def _unsupported_format_conflict(
    export: WorkspaceExportResponse,
) -> WorkspaceImportConflict | None:
    if export.manifest.format_version == SUPPORTED_WORKSPACE_EXPORT_FORMAT:
        return None
    return WorkspaceImportConflict(
        collection="manifest",
        source_id=str(export.manifest.workspace_id),
        field="format_version",
        source_value=export.manifest.format_version,
        target_value=SUPPORTED_WORKSPACE_EXPORT_FORMAT,
        strategy="reject",
        severity="error",
        message=(
            f"Workspace export format {export.manifest.format_version!r} is not supported; "
            f"expected {SUPPORTED_WORKSPACE_EXPORT_FORMAT!r}."
        ),
    )


def _disabled_skill_install_conflict(
    *,
    source_id: str,
    installed_key: str,
    status: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection="skill_installs",
        source_id=source_id,
        field="status",
        source_value=status,
        target_value="active",
        strategy="reject",
        severity="error",
        message=(
            f"Skill install {installed_key!r} is {status!r} in the source export; "
            "importing it as active would change the source workspace safety policy."
        ),
    )


def _missing_runtime_policy_conflict(
    *,
    source_id: str,
    runtime_space_name: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection="runtime_spaces",
        source_id=source_id,
        field="policy",
        source_value="{}",
        strategy="reject",
        severity="error",
        message=(
            f"Runtime space {runtime_space_name!r} has no runtime policy in the source export; "
            "import requires an explicit policy before this space can be created."
        ),
    )


def _quota_violation_conflict(
    *,
    collection: str,
    source_id: str,
    quota_key: str,
    limit_value: int,
    reserved_value: int,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field="reserved_value",
        source_value=str(reserved_value),
        target_value=str(limit_value),
        strategy="reject",
        severity="error",
        message=(
            f"Quota {quota_key!r} reserves {reserved_value}, which exceeds its limit "
            f"{limit_value}; import requires a consistent quota before commit."
        ),
    )


def _checksum_conflict(
    *,
    collection: str,
    source_id: str,
    source_checksum: str,
    actual_checksum: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection=collection,
        source_id=source_id,
        field="checksum_sha256",
        source_value=source_checksum,
        target_value=actual_checksum,
        strategy="reject",
        severity="error",
        message=(
            f"{collection[:-1].replace('_', ' ').title()} checksum mismatch for "
            f"{source_id}: expected {source_checksum}, got {actual_checksum}."
        ),
    )


def _preview_token_conflict(
    *,
    source_id: str,
    supplied_token: str,
) -> WorkspaceImportConflict:
    return WorkspaceImportConflict(
        collection="manifest",
        source_id=source_id,
        field="preview_token",
        source_value=supplied_token,
        target_value=None,
        strategy="reject",
        severity="error",
        message="Import preview token does not match the supplied metadata payload.",
    )


