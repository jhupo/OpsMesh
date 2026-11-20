import base64
import csv
import hashlib
import io
import os
import stat
import sys
import keyword
from email import message_from_bytes
from importlib.machinery import PathFinder
from pathlib import Path
from zipfile import ZipFile

from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from ..config import identifier, require
from ..storage.filesystem import atomic_write


def inspect(inbox, filename, digest, dest=None, *, plugin_root):
    files = {}
    require(
        isinstance(filename, str)
        and Path(filename).name == filename
        and len(filename) < 256,
        "Expected inbox wheel basename",
    )
    dist, ver, _, tags = parse_wheel_filename(filename)
    require(
        any(
            t in tags and t.platform == "any" and t.abi == "none"
            for t in sys_tags()
        ),
        "Incompatible wheel",
    )
    source = inbox / filename
    require(
        source.is_file() and not source.is_symlink(),
        "Wheel not found in inbox",
        "not_found",
    )
    with source.open("rb") as f:
        require(
            os.fstat(f.fileno()).st_size <= 128 << 20
            and hashlib.file_digest(f, "sha256").hexdigest() == digest,
            "Wheel size or SHA256 mismatch",
        )
        f.seek(0)
        with ZipFile(f) as z:
            names = set(z.namelist())
            require(
                len(names) == len(z.infolist()) <= 10000
                and sum(i.file_size for i in z.infolist()) <= 512 << 20,
                "Invalid wheel limits or duplicate names",
            )
            prefixes = {
                n.split("/")[0]
                for n in names
                if n.split("/")[0].endswith(".dist-info")
            }
            require(len(prefixes) == 1, "Expected one dist-info directory")
            prefix = prefixes.pop() + "/"
            meta_name, sep, meta_ver = prefix.removesuffix(
                ".dist-info/"
            ).rpartition("-")
            require(
                sep
                and canonicalize_name(meta_name) == dist
                and Version(meta_ver) == ver,
                "Invalid dist-info directory",
            )

            def read_meta(key, limit=1 << 20):
                member = z.getinfo(prefix + key)
                require(member.file_size <= limit, f"{key} too large")
                return z.read(member)

            roots = {
                part.split("/", 1)[0]
                for part in names
                if not part.startswith(prefix)
            }
            require(
                len(roots) == 1, "Expected a single top-level Python package"
            )
            name = identifier(roots.pop())
            require(
                not keyword.iskeyword(name)
                and name not in sys.stdlib_module_names
                and PathFinder.find_spec(
                    name,
                    [
                        entry
                        for entry in sys.path
                        if Path(entry).resolve() != plugin_root.resolve()
                    ],
                )
                is None,
                "Import name already belongs to host",
                "dependency_conflict",
            )
            msg = message_from_bytes(read_meta("METADATA"))
            require(
                dist == canonicalize_name(msg["Name"])
                and ver == Version(msg["Version"]),
                "Wheel metadata mismatch",
            )
            require(
                ".".join(map(str, sys.version_info[:3]))
                in SpecifierSet(msg.get("Requires-Python", "")),
                "Incompatible Python",
            )
            requirements = msg.get_all("Requires-Dist", [])
            require(
                message_from_bytes(read_meta("WHEEL"))
                .get("Root-Is-Purelib", "")
                .lower()
                == "true",
                "Pure Python wheel required",
            )
            rows = {
                path: (h, size)
                for path, h, size in csv.reader(
                    io.StringIO(read_meta("RECORD", 4 << 20).decode())
                )
            }
            require(
                name + "/__init__.py" in names,
                "Missing plugin entry point",
            )
            for i in z.infolist():
                path = i.filename.rstrip("/")
                parts = path.split("/")
                require(
                    parts[0] in (name, prefix[:-1])
                    and all(s not in ("", ".", "..") for s in parts)
                    and "\\" not in path
                    and ":" not in path,
                    "Invalid wheel path",
                )
                mode = stat.S_IFMT(i.external_attr >> 16)
                require(
                    mode in (0, stat.S_IFDIR, stat.S_IFREG)
                    and not path.lower().endswith(
                        (".pth", ".so", ".pyd", ".dll", ".pyc")
                    ),
                    "Unsupported wheel member",
                )
                if i.is_dir():
                    continue
                require(i.file_size <= 64 << 20, "Wheel member too large")
                data = z.read(i)
                if i.filename != prefix + "RECORD":
                    encoded, size = rows.get(i.filename, ("", ""))
                    algo, _, checksum = encoded.partition("=")
                    require(
                        algo in ("sha256", "sha384", "sha512"),
                        "Unsupported RECORD hash",
                    )
                    actual = (
                        base64.urlsafe_b64encode(
                            hashlib.new(algo, data).digest()
                        )
                        .decode()
                        .rstrip("=")
                    )
                    require(
                        checksum == actual and size == str(len(data)),
                        "Invalid RECORD entry",
                    )
                if parts[0] == name:
                    require(
                        len(parts) >= 2 and parts[1] != ".opsmesh-metadata",
                        "Reserved package path",
                    )
                    relative = "/".join(parts[1:])
                else:
                    relative = ".opsmesh-metadata/" + i.filename
                files[relative] = hashlib.sha256(data).hexdigest()
                if dest:
                    atomic_write(dest / relative, data)
    return {
        "version": str(ver),
        "digest": digest,
        "module": name,
        "distribution": dist,
        "requires": requirements,
        "files": files,
    }
