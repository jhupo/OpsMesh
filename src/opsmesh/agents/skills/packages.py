"""Build bounded, deterministic Skill ZIPs without importing or executing their code."""

import stat
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from opsmesh.shared.files import read_bytes_bounded, validate_relative_path

MAX_SKILL_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_SKILL_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_SKILL_ENTRIES = 4096


def build_skill_archive(directory: Path) -> bytes:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("A Skill must be a regular directory")
    manifest = directory / "SKILL.md"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("A Skill must contain a regular SKILL.md")

    output = BytesIO()
    count = 0
    remaining = MAX_SKILL_EXPANDED_BYTES
    root = directory.resolve(strict=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            count += 1
            if count > MAX_SKILL_ENTRIES:
                raise ValueError("The Skill contains too many entries")
            name = validate_relative_path(path.relative_to(directory).as_posix())
            if ":" in name or len(name) > 1000 or any(ord(char) < 32 for char in name):
                raise ValueError("The Skill contains an unsafe file path")
            mode = path.lstat().st_mode
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError("The Skill contains a path outside its directory")
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError("Skills contain only regular files and directories")
            if remaining == 0 and path.stat().st_size:
                raise ValueError("The Skill exceeds its expanded size limit")
            content = read_bytes_bounded(path, max_bytes=max(remaining, 1))
            if len(content) > remaining:
                raise ValueError("The Skill exceeds its expanded size limit")
            remaining -= len(content)
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, content)
            if output.tell() > MAX_SKILL_ARCHIVE_BYTES:
                raise ValueError("The Skill ZIP exceeds its size limit")
    if output.tell() > MAX_SKILL_ARCHIVE_BYTES:
        raise ValueError("The Skill ZIP exceeds its size limit")
    return output.getvalue()
