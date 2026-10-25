"""Generate plugin SDK bindings from the framework's public extension contracts."""

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "src/opsmesh/contracts"


def public_names(source: str) -> list[str]:
    """Read declarations without importing contracts or maintaining a second API list."""
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return sorted(name for name in names if not name.startswith("_"))


class Stub(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        return node


def contract_manifest() -> dict:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        "format": 1,
        "framework_version": project["project"]["version"],
        "sdk_version": project["tool"]["opsmesh"]["sdk"]["version"],
        "files": {
            f"opsmesh/contracts/{path.name}": hashlib.sha256(
                path.read_text(encoding="utf-8").encode()
            ).hexdigest()
            for path in sorted(CONTRACTS.glob("*.py"))
        },
    }


def obsolete_bindings(package: Path) -> list[Path]:
    """Identify removed contract modules without deleting a developer's files."""
    modules = {"channels"}  # The transport interface is owned by the gateway now.
    previous = package / "_contracts.json"
    if previous.is_file():
        for name in json.loads(previous.read_text(encoding="utf-8")).get("files", {}):
            match = re.fullmatch(r"opsmesh/contracts/([a-z][a-z0-9_]*)\.py", name)
            if match and not (CONTRACTS / (match[1] + ".py")).exists():
                modules.add(match[1])
    return [
        path
        for module in sorted(modules)
        for suffix in (".py", ".pyi")
        if (path := package / (module + suffix)).exists() or path.is_symlink()
    ]


def generated_files() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    files = {}
    initial = (CONTRACTS / "__init__.py").read_text(encoding="utf-8")
    public = next(
        ast.literal_eval(node.value)
        for node in ast.parse(initial).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )
    for path in sorted(CONTRACTS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = path.stem
        source = path.read_text(encoding="utf-8")
        names = public_names(source)
        header = f"# Generated from opsmesh/contracts/{module}.py; do not edit.\n"
        files[f"{module}.py"] = (
            header
            + f"from opsmesh.contracts.{module} import (\n"
            + "".join(f"    {name},\n" for name in names)
            + f")\n\n__all__ = {json.dumps(names)}\n"
        )
        # Packaging and static typing remain usable without installing a server.
        # This schema is data only; runtime exception/context identities are never copied.
        if module == "manifest":
            files[f"{module}.py"] = header + source
        stub = ast.fix_missing_locations(Stub().visit(ast.parse(source)))
        files[f"{module}.pyi"] = header + ast.unparse(stub) + "\n"
    files["__init__.py"] = (
        '"""Generated plugin bindings. Runtime contracts are provided by OpsMesh."""\n\n'
        "from importlib import import_module\n"
        "from typing import Any\n\n"
        f"__all__ = {json.dumps(public)}\n\n\n"
        "def __getattr__(name: str) -> Any:\n"
        "    if name not in __all__:\n"
        "        raise AttributeError(name)\n"
        '    return getattr(import_module("opsmesh.contracts"), name)\n'
    )
    files["__init__.pyi"] = initial
    files["py.typed"] = ""
    files["_contracts.json"] = json.dumps(contract_manifest(), indent=2, sort_keys=True) + "\n"
    generated = {f"src/opsmesh_sdk/{name}": content for name, content in files.items()}
    generated["README.md"] = """# OpsMesh Plugin SDK

Generated plugin bindings, standalone type declarations and a ZIP packaging CLI.
The framework owns the public definitions in `opsmesh.contracts`; runtime SDK
imports resolve to those exact types. Contexts, exceptions and suspended results
do not have a second implementation in the SDK.

## Requirements

- Python 3.11.8+ for static typing and the packaging CLI, without a running server.
- Plugins execute inside their matching Python 3.12+ OpsMesh host. Importing runtime
  contracts outside that host requires installing the matching framework package.
- Native Agents are optional for packaging. Plugins can import OpenAI Agents SDK
  directly; the SDK is not a model runner or a business HTTP API client.

## Installation

Use the matching generated wheel from an OpsMesh Release and verify SHA256SUMS.
The framework does not install or import this package as a dependency. Linux
executable bundles include generated bindings for hosted plugins. For a Python
deployment, install both the framework and the matching generated SDK wheel.

```sh
pip install ./opsmesh_sdk-0.2.0-py3-none-any.whl
opsmesh-plugin package ./my-plugin ./my-plugin.zip
```

Plugin import never installs Python dependencies automatically. Operators provision
required third-party packages before enabling a trusted plugin.

## Plugin entry point

```python
from typing import Any

from opsmesh_sdk import InvocationContext, PluginContext


async def invoke(context: InvocationContext, value: Any) -> Any:
    return {"user_id": context.user.user_id, "value": value}


def setup(context: PluginContext) -> None:
    context.entry("main", invoke)
```

`setup` declares entries, tools, MCP factories, skills, channels and sandbox
factories. It must not start connections, processes or Agent runs. Contexts expose
trusted identity, pinned dependencies, resources, sessions, checkpoints and events.
Group membership grants invocation access, never access to another user's workspace.

The SDK exposes task-level channel reply/request and contribution registration.
Transport implementations inherit `opsmesh.gateway.adapters.base.Adapter` from the
matching host and implement capabilities/run/send/call; no transport class is
generated into the SDK. Generation rejects obsolete contract modules in an existing
checkout to preserve local files. Release staging removes them only in its fresh,
verified checkout before generation.

## Source ownership

The independent `sdk` branch owns project metadata, CLI, examples and packaging.
The framework's `scripts/generate_sdk.py /path/to/sdk-checkout` generates bindings,
typing stubs, the offline manifest schema and this README. Do not edit generated
files by hand. `_contracts.json` records framework version and source digests;
release automation generates bindings from the framework being released, not a
second hand-maintained set of contracts. The offline manifest contains data-only
validation; runtime exceptions and results always resolve to framework classes.

License: MIT. See LICENSE and the OpsMesh repository for contribution rules.
""".replace("opsmesh_sdk-0.2.0", f"opsmesh_sdk-{project['tool']['opsmesh']['sdk']['version']}")
    generated["AGENTS.md"] = """# Plugin SDK development

The independent sdk branch owns project metadata, the packaging CLI and examples.
Public contracts belong to the framework's opsmesh.contracts package. Regenerate
bindings and stubs with the matching framework's scripts/generate_sdk.py; never
maintain a second implementation or edit generated files by hand. Runtime bindings
may import only the framework's public contracts, not business implementation modules.

- Preserve unrelated changes and data. Do not commit, push or publish automatically.
- Tests, collection, service starts and model calls require explicit user authorization.
  Static checks and packaging are allowed; do not call unverified behavior passed.
- Packaging and typing support Python 3.11.8+; plugin execution requires the matching
  Python 3.12+ framework. Keep package initialization lazy for offline packaging.
- setup(ctx) declares contributions and does not start I/O, processes or Agent runs.
  Trusted platform identity comes from the host; metadata cannot elevate permissions.
- Prefer native OpenAI Agents SDK behavior. Do not add a business API client.
- Run ruff format --check src examples, ruff check src examples and mypy using this
  project's configuration. uv build produces wheel/sdist; it does not prove runtime
  behavior. Keep README, examples and package metadata aligned. License is MIT.
"""
    generated["CONTRIBUTING.md"] = """# Contributing

Develop packaging CLI, project metadata and examples on the independent sdk branch.
Change public types in the framework's src/opsmesh/contracts, then run its
scripts/generate_sdk.py against this checkout. Runtime bindings export those exact
framework types; generated typing stubs and the data-only manifest support offline
development. Do not add duplicate exception/context implementations or compatibility
aliases. This SDK is not an HTTP API client.

Follow AGENTS.md for static checks and explicit test authorization. Use Python
3.11.8+ for packaging/type checks; runtime plugins execute in a Python 3.12+ host.
Release automation stages a pinned SDK project, generates bindings from the tagged
framework and builds both distributions. _contracts.json records the source digests.
No PyPI publication is configured. Do not commit, push, tag or publish automatically.
Contributions use MIT; security reports follow SECURITY.md.
"""
    return generated


def generate(project_root: Path, *, check: bool = False) -> None:
    project_root = project_root.resolve()
    project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    expected = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if (
        project_root == ROOT
        or project["project"]["name"] != "opsmesh-sdk"
        or project["project"]["version"] != expected["tool"]["opsmesh"]["sdk"]["version"]
    ):
        raise ValueError("Expected a separate SDK checkout matching the release version")
    package = project_root / "src/opsmesh_sdk"
    if (
        not package.is_dir()
        or package.is_symlink()
        or not package.resolve().is_relative_to(project_root)
    ):
        raise ValueError("SDK checkout must contain its own src/opsmesh_sdk package")
    files = generated_files()
    obsolete = obsolete_bindings(package)
    if obsolete:
        raise ValueError(f"Obsolete SDK bindings must be reviewed before generation: {obsolete}")
    # Use the locked build formatter for deterministic, checked-in-quality output.
    # These are source transformations only; no generated plugin code is executed.
    with TemporaryDirectory(prefix="opsmesh-sdk-") as temporary:
        staging = Path(temporary)
        python_files = []
        for name, content in files.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            if path.suffix in {".py", ".pyi"}:
                python_files.append(str(path))
        command = [sys.executable, "-m", "ruff"]
        config = ["--config", str(project_root / "pyproject.toml")]
        subprocess.run(
            [*command, "check", "--fix", *config, *python_files], check=True, capture_output=True
        )
        subprocess.run(
            [*command, "format", *config, *python_files], check=True, capture_output=True
        )
        files = {name: (staging / name).read_text(encoding="utf-8") for name in files}
    for name, content in files.items():
        target = project_root / name
        if target.is_symlink():
            raise ValueError("Generated SDK files cannot be links")
        if check:
            if not target.is_file() or target.read_text(encoding="utf-8") != content:
                raise ValueError(f"Generated SDK contract differs: {name}")
        else:
            target.write_text(content, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Separate SDK project checkout")
    parser.add_argument("--check", action="store_true", help="Check generated files without writes")
    args = parser.parse_args()
    generate(args.project, check=args.check)


if __name__ == "__main__":
    main()
