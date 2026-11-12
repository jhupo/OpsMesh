"""Bounded archive imports, directory copies, and ZIP exports."""

from __future__ import annotations

import shutil
import unicodedata
import zipfile
from pathlib import Path
from typing import BinaryIO

from ._paths import relative_path_parts
from .config import Settings
from .errors import ArchiveRejected, PathRejected

_CHUNK_SIZE = 1024 * 1024
_FILE_TYPE_MASK = 0o170000
_REGULAR_FILE = 0o100000
_DIRECTORY = 0o040000
_SYMLINK = 0o120000


def stage_stream(source: BinaryIO, destination: Path, *, max_bytes: int) -> int:
    """Copy an upload stream to a staging file while enforcing a limit."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with destination.open("xb") as target:
            while True:
                chunk = source.read(_CHUNK_SIZE)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise ArchiveRejected("The archive stream must be binary")
                total += len(chunk)
                if total > max_bytes:
                    raise ArchiveRejected(
                        "The archive exceeds the configured size limit"
                    )
                target.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if total == 0:
        destination.unlink(missing_ok=True)
        raise ArchiveRejected("The archive is empty")
    return total


def extract_archive(
    archive_path: Path, destination: Path, settings: Settings
) -> None:
    """Validate and extract a ZIP archive without trusting member names."""
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members:
                raise ArchiveRejected("The archive contains no entries")
            if len(members) > settings.max_entries:
                raise ArchiveRejected("The archive contains too many entries")
            plans = _plan_members(members, settings)
            declared_total = sum(
                info.file_size
                for info, (_, is_directory) in plans
                if not is_directory
            )
            if declared_total > settings.max_extracted_bytes:
                raise ArchiveRejected(
                    "The archive expands beyond the configured limit"
                )
            total = 0
            for info, (parts, is_directory) in plans:
                target = destination.joinpath(*parts)
                if is_directory:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                total += _extract_file(archive, info, target, settings, total)
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ArchiveRejected("The archive is not a valid ZIP file") from error
    except NotImplementedError as error:
        raise ArchiveRejected(
            "The archive uses an unsupported compression method"
        ) from error
    except OSError as error:
        raise ArchiveRejected("The archive could not be extracted") from error
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if not any(path.is_file() for path in destination.rglob("*")):
        shutil.rmtree(destination, ignore_errors=True)
        raise ArchiveRejected("The archive contains no regular files")


def _plan_members(
    members: list[zipfile.ZipInfo], settings: Settings
) -> list[tuple[zipfile.ZipInfo, tuple[tuple[str, ...], bool]]]:
    plans: list[tuple[zipfile.ZipInfo, tuple[tuple[str, ...], bool]]] = []
    files: set[str] = set()
    directories: set[str] = set()
    seen: set[str] = set()
    for info in members:
        if info.flag_bits & 0x1:
            raise ArchiveRejected("Encrypted archives are not supported")
        parts, is_directory = archive_member_parts(
            info.filename,
            max_length=settings.max_path_length,
            max_depth=settings.max_depth,
        )
        _validate_file_type(info, is_directory)
        if not is_directory and info.file_size > settings.max_file_bytes:
            raise ArchiveRejected("The archive contains an oversized file")
        key = normalize_key(parts)
        if key in seen:
            raise ArchiveRejected("The archive contains colliding member names")
        seen.add(key)
        if is_directory:
            if key in files:
                raise ArchiveRejected(
                    "The archive uses one path as a file and a directory"
                )
            directories.add(key)
        else:
            if key in directories or key in files:
                raise ArchiveRejected(
                    "The archive uses one path as a file and a directory"
                )
            prefix = key + "/"
            if any(existing.startswith(prefix) for existing in files):
                raise ArchiveRejected(
                    "The archive uses one path as a file and a directory"
                )
            for index in range(1, len(parts)):
                parent = normalize_key(parts[:index])
                if parent in files:
                    raise ArchiveRejected(
                        "The archive uses one path as a file and a directory"
                    )
            files.add(key)
        plans.append((info, (parts, is_directory)))
    return plans


def _validate_file_type(info: zipfile.ZipInfo, is_directory: bool) -> None:
    mode = info.external_attr >> 16
    file_type = mode & _FILE_TYPE_MASK
    if file_type == _SYMLINK:
        raise ArchiveRejected("The archive contains a symbolic link")
    if file_type not in (0, _REGULAR_FILE, _DIRECTORY):
        raise ArchiveRejected("The archive contains an unsupported file type")
    if is_directory and file_type not in (0, _DIRECTORY):
        raise ArchiveRejected("The archive contains a malformed directory")
    if not is_directory and file_type == _DIRECTORY:
        raise ArchiveRejected("The archive contains a malformed file")


def _extract_file(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    target: Path,
    settings: Settings,
    total_so_far: int,
) -> int:
    written = 0
    with archive.open(info) as reader, target.open("xb") as writer:
        while True:
            chunk = reader.read(_CHUNK_SIZE)
            if not chunk:
                break
            written += len(chunk)
            if written > settings.max_file_bytes:
                raise ArchiveRejected("The archive contains an oversized file")
            if total_so_far + written > settings.max_extracted_bytes:
                raise ArchiveRejected(
                    "The archive expands beyond the configured limit"
                )
            writer.write(chunk)
    return written


def copy_tree(source: Path, destination: Path, settings: Settings) -> None:
    """Copy a controlled local directory with the same structural limits."""
    if not source.is_dir() or source.is_symlink():
        raise PathRejected("The source must be a regular directory")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        total = 0
        count = 0
        for path in sorted(source.rglob("*")):
            count += 1
            if count > settings.max_entries:
                raise PathRejected("The source contains too many entries")
            relative = path.relative_to(source).as_posix()
            if len(relative) > settings.max_path_length:
                raise PathRejected("The source contains an overly long path")
            relative_path_parts(
                relative,
                max_length=settings.max_path_length,
                max_depth=settings.max_depth,
            )
            target = destination.joinpath(*path.relative_to(source).parts)
            if path.is_symlink():
                raise PathRejected("The source contains a symbolic link")
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not path.is_file():
                raise PathRejected("The source contains a special file")
            size = path.stat().st_size
            if size > settings.max_file_bytes:
                raise PathRejected("The source contains an oversized file")
            total += size
            if total > settings.max_extracted_bytes:
                raise PathRejected("The source is too large")
            target.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, _CHUNK_SIZE)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def write_archive(source: Path, destination: Path) -> int:
    """Write a ZIP snapshot of a validated content directory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with zipfile.ZipFile(
            destination, "x", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise PathRejected("The content contains a symbolic link")
                relative = path.relative_to(source).as_posix()
                if path.is_dir():
                    info = zipfile.ZipInfo(relative + "/")
                    info.external_attr = (_DIRECTORY | 0o755) << 16
                    archive.writestr(info, b"")
                elif path.is_file():
                    archive.write(path, relative)
                    written += path.stat().st_size
                else:
                    raise PathRejected("The content contains a special file")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return written


def archive_member_parts(
    name: str, *, max_length: int, max_depth: int
) -> tuple[tuple[str, ...], bool]:
    """Validate one ZIP member name and report whether it is a directory."""
    is_directory = name.endswith("/")
    parts = relative_path_parts(
        name,
        max_length=max_length,
        max_depth=max_depth,
        error=ArchiveRejected,
    )
    return parts, is_directory


def normalize_key(parts: tuple[str, ...]) -> str:
    """Build a collision key folding Unicode composition and case."""
    normalized = []
    for part in parts:
        component = unicodedata.normalize("NFC", part)
        component = unicodedata.normalize("NFC", component.casefold())
        normalized.append(component)
    return "/".join(normalized)
