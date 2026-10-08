from pathlib import PurePosixPath
from urllib.parse import quote, unquote

_FILENAME_RESERVED_CHARS = set('\x00\r\n/\\:*?"<>|')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_filename(raw_filename: str, *, default: str = "upload.bin") -> str:
    candidate = unquote(raw_filename).strip().replace("\\", "/").split("/")[-1].strip()
    if not candidate or candidate in {".", ".."}:
        candidate = default
    cleaned = "".join("_" if char in _FILENAME_RESERVED_CHARS else char for char in candidate)
    cleaned = cleaned.strip(" .")
    if not cleaned:
        cleaned = default
    stem = cleaned.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:260]


def content_disposition_attachment(filename: str) -> str:
    sanitized = safe_filename(filename)
    ascii_fallback = "".join(char if 32 <= ord(char) < 127 else "_" for char in sanitized)
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "_")
    encoded = quote(sanitized)
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def validate_storage_key(storage_key: str, *, expected_prefix: str | None = None) -> str:
    if not storage_key:
        raise ValueError("Storage key is required")
    if "\x00" in storage_key or "\\" in storage_key:
        raise ValueError("Storage key contains unsafe characters")
    path = PurePosixPath(storage_key)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Storage key escapes storage root")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ValueError("Storage key is invalid")
    if expected_prefix is not None and not normalized.startswith(expected_prefix.rstrip("/") + "/"):
        raise ValueError("Storage key is outside the expected workspace prefix")
    return normalized


def validate_runtime_relative_path(relative_path: str) -> str:
    if not relative_path or "\x00" in relative_path:
        raise ValueError("Runtime path is invalid")
    normalized = PurePosixPath(relative_path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() in {"", "."}:
        raise ValueError("Runtime staging path escapes runtime root")
    return normalized.as_posix()
