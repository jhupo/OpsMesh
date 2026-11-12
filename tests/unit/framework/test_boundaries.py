import ast
import importlib.util
import subprocess
import sys
from graphlib import TopologicalSorter
from pathlib import Path

from opsmesh import framework
from opsmesh.framework.config import Config
from opsmesh.framework.permissions import MESSAGE, Permission
from opsmesh.framework.plugins import load_from_toml
from opsmesh.framework.rules import Rule, regex


def test_core_import_does_not_load_platform_or_transport_implementations():
    script = """
import importlib.abc
import sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        transports = {'httpx', 'aiohttp', 'fastapi', 'quart', 'websockets',
                      'loguru', 'pydantic_settings', 'dotenv', 'agents'}
        if (fullname.startswith(('opsmesh.gateway', 'opsmesh.infra',
                                 'opsmesh.modules', 'opsmesh.integrations'))
                or fullname in transports):
            raise AssertionError('Forbidden core dependency: ' + fullname)
sys.meta_path.insert(0, Boundary())
import opsmesh.framework
import opsmesh.framework.dependencies
import opsmesh.framework.plugins
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_core_config_does_not_choose_a_server():
    config = Config()
    assert {"driver", "host", "port"}.isdisjoint(type(config).model_fields)


def test_framework_does_not_expose_host_entrypoints():
    assert not hasattr(framework, "init")
    assert not hasattr(framework, "run")
    assert not hasattr(framework, "logger")


def test_framework_does_not_create_or_run_asgi_hosts():
    root = Path(framework.__file__).resolve().parent.parent
    violations = []
    forbidden_calls = {
        "FastAPI",
        "fastapi.FastAPI",
        "uvicorn.run",
        "asyncio.run",
        "anyio.run",
        "signal.signal",
    }
    for package in ("framework",):
        for path in (root / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and ast.unparse(node.func) in forbidden_calls
                ):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert violations == []


def test_public_predicates_remain_usable_after_package_split():
    assert all(
        (
            isinstance(MESSAGE, Permission),
            isinstance(regex("hello"), Rule),
        )
    )


def test_plugin_configuration_uses_nested_opsmesh_table(tmp_path):
    path = tmp_path / "plugins.toml"
    path.write_text(
        "[tool.opsmesh.framework]\nplugins = []\nplugin_dirs = []\n",
        encoding="utf-8",
    )
    assert load_from_toml(str(path)) == set()


def _import_targets(path, source_root):
    module = ".".join(path.relative_to(source_root).with_suffix("").parts)
    package = module.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = importlib.util.resolve_name(
                    "." * node.level + base, package
                )
            yield base
            for alias in node.names:
                yield f"{base}.{alias.name}"


def test_framework_dependencies_follow_layer_boundaries():
    package = "framework"
    forbidden_prefixes = (
        "opsmesh.plugins",
        "opsmesh_channels",
        "opsmesh_database",
        "opsmesh_execution",
        "opsmesh_filesystem",
        "opsmesh.app",
        "opsmesh.api",
        "opsmesh.modules",
        "opsmesh.gateway",
        "opsmesh.infra",
        "opsmesh.integrations",
        "agents",
        "openai",
        "mcp",
        "httpx",
        "fastapi",
        "pydantic_settings",
        "dotenv",
    )
    source_root = Path(framework.__file__).resolve().parents[2]
    root = source_root / "opsmesh" / package
    forbidden = []
    for path in root.rglob("*.py"):
        for target in _import_targets(path, source_root):
            if any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in forbidden_prefixes
            ):
                forbidden.append(f"{path.relative_to(source_root)} -> {target}")
    assert forbidden == []


def test_framework_has_no_sdk_import_exceptions():
    root = Path(framework.__file__).resolve().parent
    source_root = root.parents[1]
    violations = []
    for path in root.rglob("*.py"):
        if any(
            target.split(".", 1)[0] in {"agents", "openai", "mcp"}
            for target in _import_targets(path, source_root)
        ):
            violations.append(str(path.relative_to(root)))
    assert violations == []


def test_internal_components_do_not_import_public_framework_entrypoint():
    root = Path(framework.__file__).resolve().parent
    source_root = root.parents[1]
    forbidden = []
    for path in root.rglob("*.py"):
        if path == root / "__init__.py":
            continue
        if "opsmesh.framework" in _import_targets(path, source_root):
            forbidden.append(str(path.relative_to(source_root)))
    assert forbidden == []


def test_framework_settings_exclude_logging_and_channel_policy():
    fields = Config.model_fields
    assert {"log_level", "superusers", "nickname"}.isdisjoint(fields)


def test_package_entrypoints_do_not_contain_implementations():
    root = Path(framework.__file__).resolve().parent
    implementations = []
    for path in root.rglob("__init__.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.name == "__getattr__"
                ):
                    continue
                implementations.append(f"{path.relative_to(root)}:{node.name}")
    assert implementations == []


class EagerImports(ast.NodeVisitor):
    def __init__(self, package):
        self.package = package
        self.targets = set()

    def visit_If(self, node):
        if ast.unparse(node.test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_FunctionDef(self, node):
        pass

    def visit_AsyncFunctionDef(self, node):
        pass

    def visit_Import(self, node):
        self.targets.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node):
        base = node.module or ""
        if node.level:
            base = importlib.util.resolve_name(
                "." * node.level + base, self.package
            )
        self.targets.add(base)
        self.targets.update(f"{base}.{alias.name}" for alias in node.names)


def test_explicit_module_body_imports_are_acyclic():
    root = Path(framework.__file__).resolve().parent
    source_root = root.parents[1]
    modules = {
        ".".join(
            path.relative_to(source_root).with_suffix("").parts
        ).removesuffix(".__init__"): path
        for path in root.rglob("*.py")
    }
    graph = {}
    for name, path in modules.items():
        package = (
            name if path.name == "__init__.py" else name.rpartition(".")[0]
        )
        imports = EagerImports(package)
        imports.visit(ast.parse(path.read_text(encoding="utf-8")))
        graph[name] = imports.targets & modules.keys()
    assert len(tuple(TopologicalSorter(graph).static_order())) == len(modules)
