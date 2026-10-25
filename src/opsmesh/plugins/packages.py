import hashlib
import io
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.contracts.manifest import PluginManifest
from opsmesh.infra.database import new_id
from opsmesh.shared.errors import ConflictError, InvalidArgumentError

from .models import Plugin


def package_path(root: Path, plugin_id: str) -> Path:
    from uuid import UUID

    return root / "packages" / str(UUID(plugin_id))


def install(session: Session, root: Path, owner_id: str, content: bytes, limit: int) -> Plugin:
    if len(content) > limit:
        raise InvalidArgumentError("Package exceeds the upload limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        members = archive.infolist()
        if len(members) > 2000 or sum(item.file_size for item in members) > limit:
            raise InvalidArgumentError("Package expands beyond its size or entry limit")
        seen: set[str] = set()
        for item in members:
            path = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if (
                item.orig_filename != item.filename
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in item.filename
                or ":" in item.filename
                or stat.S_ISLNK(mode)
                or item.flag_bits & 1
                or not path.parts
                or any(
                    part.rstrip(" .") != part
                    or part.split(".")[0].upper()
                    in {
                        "CON",
                        "PRN",
                        "AUX",
                        "NUL",
                        *(f"COM{i}" for i in range(10)),
                        *(f"LPT{i}" for i in range(10)),
                    }
                    for part in path.parts
                )
                or item.filename.casefold() in seen
            ):
                raise InvalidArgumentError("Unsafe or duplicate package path")
            seen.add(item.filename.casefold())
        raw = archive.read("plugin.yaml")
        if len(raw) > 64000 or "main.py" not in archive.namelist():
            raise InvalidArgumentError("A package requires plugin.yaml and main.py at its root")
        manifest = PluginManifest.model_validate(yaml.safe_load(raw))
    except (zipfile.BadZipFile, KeyError, ValueError, yaml.YAMLError) as exc:
        raise InvalidArgumentError("Invalid plugin archive or manifest") from exc
    existing = session.scalar(
        select(Plugin).where(
            Plugin.owner_id == owner_id,
            Plugin.name == manifest.id,
            Plugin.version == manifest.version,
        )
    )
    if existing is not None:
        raise ConflictError("Plugin versions are immutable")
    row = Plugin(
        id=new_id(),
        owner_id=owner_id,
        name=manifest.id,
        version=manifest.version,
        digest=hashlib.sha256(content).hexdigest(),
        manifest=manifest.model_dump(mode="json"),
    )
    destination = package_path(root, row.id)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for item in members:
            target = destination.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        session.add(row)
        session.flush()
    except BaseException:
        if destination.resolve() == (
            root / "packages"
        ).resolve() or not destination.resolve().is_relative_to((root / "packages").resolve()):
            raise ValueError("Package cleanup target escapes the package directory") from None
        shutil.rmtree(destination)
        raise
    finally:
        archive.close()
    return row
