"""Freeze the framework host and Python standard library, without plugins."""

import importlib.util
import importlib.metadata
import sys
from pathlib import Path

from packaging.utils import canonicalize_name
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

root = Path(SPECPATH).parent
source_path = str(root / "src")
sys.path.insert(0, source_path)
sys.path.insert(0, str(root / "scripts"))
from release_artifacts import runtime_dependencies

# Plugins may use public submodules that core itself never imports.
host_distributions = {
    canonicalize_name(item["name"]) for item in runtime_dependencies("opsmesh")
}
host_packages = {
    name
    for name, owners in importlib.metadata.packages_distributions().items()
    if any(canonicalize_name(owner) in host_distributions for owner in owners)
}
host_modules = []
host_data = copy_metadata("opsmesh", recursive=True)
for name in sorted(host_packages):
    host_modules.extend(collect_submodules(name))
    host_data.extend(collect_data_files(name))

# External plugins may import stdlib modules unused by the host itself.
excluded_stdlib = {
    "__main__",
    "_testcapi",
    "_testinternalcapi",
    "_tkinter",
    "antigravity",
    "ensurepip",
    "idlelib",
    "lib2to3",
    "test",
    "this",
    "tkinter",
    "turtle",
    "turtledemo",
    "venv",
}
standard_library = []
for name in sorted(sys.stdlib_module_names - excluded_stdlib):
    spec = importlib.util.find_spec(name)
    if spec is None:
        continue
    standard_library.append(name)
    if spec.submodule_search_locations is not None:
        standard_library.extend(
            collect_submodules(name, filter=lambda item: ".test" not in item)
        )

analysis = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[source_path],
    binaries=[],
    datas=host_data,
    hiddenimports=host_modules + standard_library,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "pytest",
        "ruff",
        "tkinter",
        "agents",
        "openai",
        "langfuse",
        "sqlalchemy",
        "alembic",
        "asyncpg",
        "uvicorn",
        "opsmesh.plugins.channels",
        "opsmesh.plugins.database",
        "opsmesh.plugins.execution",
        "opsmesh.plugins.filesystem",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="opsmesh",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="opsmesh",
)
