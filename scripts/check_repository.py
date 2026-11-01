"""Validate local documentation links and static source import boundaries."""

from __future__ import annotations

import ast
import re
from importlib.util import resolve_name
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "opsmesh"
SOURCE_LAYERS = frozenset(
    {
        "framework",
        "modules",
        "infrastructure",
        "bootstrap",
        "application",
        "entrypoints",
        "maintenance",
        "plugins",
    }
)
PUBLIC_ROOT_FILES = frozenset({"__init__.py", "__main__.py", "_version.py", "dependencies.py"})


def check_documentation() -> list[str]:
    """Check authored Markdown, excluding build output and virtual environments."""
    errors: list[str] = []
    paths = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        if len(re.findall(r"^```", content, flags=re.MULTILINE)) % 2:
            errors.append(f"{path.relative_to(ROOT)}: unbalanced code fences")
        prose = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", prose):
            target = match[1].strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path or target.startswith("//"):
                continue
            destination = (path.parent / unquote(parsed.path)).resolve()
            if not destination.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing link target {target}")
    return errors


def check_imports() -> list[str]:
    """Enforce statically visible boundaries; dynamic imports require review."""
    errors: list[str] = []
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE)
        errors.extend(find_import_violations(relative, path.read_text(encoding="utf-8")))
    return errors


def check_package_contents() -> list[str]:
    """Do not ship leaf packages that merely describe an unimplemented future feature."""
    errors = []
    for initializer in SOURCE.rglob("__init__.py"):
        if len(list(initializer.parent.rglob("*.py"))) != 1:
            continue
        body = ast.parse(initializer.read_text(encoding="utf-8")).body
        executable = [
            node
            for node in body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        if not executable:
            errors.append(f"{initializer.parent.relative_to(ROOT)}: empty placeholder package")
    return errors


def check_source_layout() -> list[str]:
    """Require a named architectural owner for source, ignoring caches and directory shells."""
    errors = []
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE)
        if len(relative.parts) == 1:
            if relative.name not in PUBLIC_ROOT_FILES:
                errors.append(f"{relative}: implementation needs an owning source layer")
        elif relative.parts[0] not in SOURCE_LAYERS:
            errors.append(f"{relative}: unknown source layer")
    return errors


def find_import_violations(relative: Path, content: str) -> list[str]:
    """Resolve absolute, relative and member imports before checking ownership."""
    errors: set[str] = set()
    layer = relative.parts[0]
    current_module = relative.parts[1] if layer == "modules" and len(relative.parts) > 2 else None
    bot_implementation = relative.parts[:2] == ("infrastructure", "bots")
    package = ".".join(("opsmesh", *relative.parent.parts))
    for node in ast.walk(ast.parse(content)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                try:
                    base = resolve_name("." * node.level + base, package)
                except ImportError:
                    errors.add(f"{relative}:{node.lineno}: relative import leaves package")
                    continue
            targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        for target in targets:
            if layer in {"framework", "modules", "infrastructure"} and target in {
                "opsmesh.OpsMeshApplication",
                "opsmesh.OpsMeshSettings",
                "opsmesh.create_application",
                "opsmesh.run",
            }:
                errors.add(f"{relative}:{node.lineno}: implementation imports public platform host")
            if layer == "framework" and (target == "examples" or target.startswith("examples.")):
                errors.add(f"{relative}:{node.lineno}: framework imports an example application")
            if relative.parts[:3] == ("modules", "bots", "conversations") and any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in (
                    "sqlalchemy",
                    "aiosqlite",
                    "sqlite3",
                    "opsmesh.infrastructure.database",
                )
            ):
                errors.add(f"{relative}:{node.lineno}: public inbox contract imports persistence")
            if layer in {
                "framework",
                "modules",
                "bootstrap",
                "application",
                "entrypoints",
                "infrastructure",
                "maintenance",
            } and (target == "opsmesh.plugins" or target.startswith("opsmesh.plugins.")):
                errors.add(f"{relative}:{node.lineno}: platform imports optional plugin code")
            if (layer == "framework" or bot_implementation) and (
                target == "opsmesh.modules" or target.startswith("opsmesh.modules.")
            ):
                errors.add(
                    f"{relative}:{node.lineno}: framework or adapter imports business modules"
                )
            if layer in {"framework", "modules"} and (
                target == "opsmesh.infrastructure.bots"
                or target.startswith("opsmesh.infrastructure.bots.")
            ):
                errors.add(
                    f"{relative}:{node.lineno}: abstract layer imports concrete bot adapters"
                )
            if layer in {"framework", "modules", "infrastructure"} and any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in (
                    "opsmesh.bootstrap",
                    "opsmesh.application",
                    "opsmesh.entrypoints",
                    "opsmesh.maintenance",
                )
            ):
                errors.add(f"{relative}:{node.lineno}: implementation imports application assembly")
            if layer == "application" and any(
                target == prefix or target.startswith(prefix + ".")
                for prefix in ("opsmesh.bootstrap", "opsmesh.entrypoints", "opsmesh.maintenance")
            ):
                errors.add(
                    f"{relative}:{node.lineno}: application imports composition or entrypoint"
                )
            if layer == "framework" and (
                target == "opsmesh.infrastructure" or target.startswith("opsmesh.infrastructure.")
            ):
                errors.add(f"{relative}:{node.lineno}: framework imports platform infrastructure")
            if layer == "modules" and (
                target == "opsmesh.infrastructure.run_workers"
                or target.startswith("opsmesh.infrastructure.run_workers.")
            ):
                errors.add(
                    f"{relative}:{node.lineno}: business module imports concrete Run workers"
                )
            if target == "agents" or target.startswith("agents."):
                backend = ("modules", "runtime", "internal", "backends", "openai_agents")
                if relative.parts[:5] != backend:
                    errors.add(f"{relative}:{node.lineno}: SDK import outside its backend")
            parts = target.split(".")
            if len(parts) >= 4 and parts[:2] == ["opsmesh", "modules"] and parts[3] == "internal":
                if parts[2] != current_module and relative.parts[0] != "bootstrap":
                    errors.add(f"{relative}:{node.lineno}: cross-module internal import")
                if parts[2] == current_module and relative.name == "api.py":
                    errors.add(f"{relative}:{node.lineno}: public API imports its implementation")
    return sorted(errors)


def main() -> int:
    errors = (
        check_documentation() + check_imports() + check_package_contents() + check_source_layout()
    )
    if errors:
        print("\n".join(errors))
        return 1
    print("Documentation, static import boundaries, source ownership and package contents passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
