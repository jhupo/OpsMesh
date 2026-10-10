"""Read application source and emit reproducible structural-review evidence only."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import grimp

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/opsmesh"
OUTPUT = ROOT / ".tmp/app-layout-review"
BUSINESS_ROOTS = {
    "identity",
    "workspaces",
    "agents",
    "teams",
    "capabilities",
    "resources",
    "orchestration",
    "runtime",
    "platform",
    "messaging",
    "governance",
}
ENTRYPOINTS = {
    "opsmesh.main",
    "opsmesh.delivery",
    "opsmesh.runtime.workers.cli",
    "opsmesh.platform.updates.daemon",
}


def is_http_route(module: str) -> bool:
    parts = module.split(".")
    return "routes" in parts or parts[-1].endswith("_routes")


def check_architecture() -> None:
    """Check all current modules; new feature files are covered automatically."""
    violations: list[str] = []
    tables: dict[str, str] = {}
    handlers: dict[str, str] = {}
    files = sorted(APP.rglob("*.py"))
    for path in files:
        module = module_name(path)
        relative = path.relative_to(APP)
        if relative.parts[0] not in BUSINESS_ROOTS | {"shared", "bootstrap"} and path.name not in {
            "__init__.py",
            "main.py",
            "delivery.py",
        }:
            violations.append(f"Unclassified application file: {relative}")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        dependencies = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                dependencies.append(node.module)
            elif isinstance(node, ast.Import):
                dependencies.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ClassDef):
                for declaration in node.body:
                    if isinstance(declaration, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "__tablename__"
                        for target in declaration.targets
                    ):
                        table = ast.literal_eval(declaration.value)
                        if table in tables:
                            violations.append(f"Duplicate table {table}: {tables[table]}, {module}")
                        tables[table] = module
                if node.name.endswith("JobHandler") and node.name not in {
                    "WorkerJobHandler",
                    "JobHandler",
                }:
                    if node.name in handlers:
                        violations.append(f"Duplicate job handler: {node.name}")
                    handlers[node.name] = module
                    if module.startswith("opsmesh.runtime.workers."):
                        violations.append(f"Business job handler retained in worker: {module}")
        for dependency in dependencies:
            if dependency.startswith(
                tuple("opsmesh." + name for name in ("api", "domains", "core", "observability"))
            ):
                violations.append(f"Legacy import: {module} -> {dependency}")
            if not dependency.startswith("opsmesh."):
                continue
            owner = dependency.split(".")[1]
            if relative.parts[0] == "shared" and owner != "shared":
                violations.append(f"Shared imports business: {module} -> {dependency}")
            if (
                owner == "bootstrap"
                and relative.parts[0] != "bootstrap"
                and module not in ENTRYPOINTS
            ):
                violations.append(f"Business imports composition: {module} -> {dependency}")
            if is_http_route(dependency) and not (
                is_http_route(module) or relative.parts[0] == "bootstrap" or module in ENTRYPOINTS
            ):
                violations.append(f"Business imports HTTP route: {module} -> {dependency}")
    for name in ("api", "domains", "core", "observability", "runtime/workers/handlers"):
        if (APP / name).exists():
            violations.append(f"Superseded directory remains: {name}")
    if violations:
        raise SystemExit("\n".join(violations))
    print(
        f"Architecture verified: {len(files)} source files, {len(tables)} unique table owners, "
        f"{len(handlers)} uniquely owned job handlers."
    )


def expression(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else ""


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def inspect_file(path: Path, graph: grimp.ImportGraph) -> dict[str, object]:
    data = path.read_bytes()
    source = data.decode("utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    module = module_name(path)
    imports = []
    declarations = []
    commits = []
    calls = Counter()
    private_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported = "." * node.level + (node.module or "")
            imports.append(
                {
                    "module": imported,
                    "names": [expression(x) for x in node.names],
                    "line": node.lineno,
                }
            )
            private_imports.extend(
                f"{imported}:{alias.name}"
                for alias in node.names
                if alias.name.startswith("_") and not alias.name.startswith("__")
            )
        elif isinstance(node, ast.Import):
            imports.extend(
                {"module": alias.name, "names": [], "line": node.lineno} for alias in node.names
            )
        elif isinstance(node, ast.Call):
            name = expression(node.func)
            calls[name] += 1
            if name.endswith((".commit", ".rollback", ".flush")):
                commits.append({"call": name, "line": node.lineno})
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            declarations.append(
                {
                    "kind": "class",
                    "name": node.name,
                    "line": node.lineno,
                    "end": node.end_lineno,
                    "bases": [expression(x) for x in node.bases],
                    "methods": [
                        {
                            "name": method.name,
                            "line": method.lineno,
                            "end": method.end_lineno,
                            "signature": expression(method.args),
                            "returns": expression(method.returns),
                        }
                        for method in node.body
                        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                    ],
                }
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            declarations.append(
                {
                    "kind": "function",
                    "name": node.name,
                    "line": node.lineno,
                    "end": node.end_lineno,
                    "signature": expression(node.args),
                    "returns": expression(node.returns),
                }
            )
    forwarder = bool(imports) and all(
        isinstance(node, ast.ImportFrom | ast.Import)
        or isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        or isinstance(node, ast.Assign)
        and all(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        for node in tree.body
    )
    edges = (
        sorted(graph.find_modules_directly_imported_by(module)) if module in graph.modules else []
    )
    callers = (
        sorted(graph.find_modules_that_directly_import(module)) if module in graph.modules else []
    )
    return {
        "path": path.relative_to(APP).as_posix(),
        "module": module,
        "sha256": hashlib.sha256(data).hexdigest(),
        "lines": len(source.splitlines()),
        "imports": imports,
        "declarations": declarations,
        "transactions": commits,
        "calls": dict(calls),
        "private_imports": private_imports,
        "forwarder": forwarder,
        "dependencies": edges,
        "callers": callers,
    }


def review_facts() -> None:
    inventory = json.loads((OUTPUT / "source-inventory.json").read_text(encoding="utf-8"))
    records = inventory["files"]
    facts = {
        "package_initializers": [
            {
                "path": r["path"],
                "lines": r["lines"],
                "declarations": r["declarations"],
                "imports": r["imports"],
            }
            for r in records
            if r["path"].endswith("__init__.py")
            and (r["imports"] or r["declarations"] or r["lines"] > 3)
        ],
        "no_backend_importers": [
            {"path": r["path"], "lines": r["lines"]}
            for r in records
            if not r["callers"] and not r["path"].endswith("__init__.py")
        ],
        "private_imports": {
            r["path"]: len(r["private_imports"]) for r in records if r["private_imports"]
        },
        "multiple_inheritance": [
            {"path": r["path"], "name": d["name"], "bases": d["bases"]}
            for r in records
            for d in r["declarations"]
            if d["kind"] == "class" and len(d["bases"]) > 1 and "Base" not in d["bases"]
        ],
        "large_files": [
            {"path": r["path"], "lines": r["lines"]} for r in records if r["lines"] >= 600
        ],
        "non_python_files": [
            p.relative_to(APP).as_posix()
            for p in APP.rglob("*")
            if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
        ],
        "shared_business_dependencies": {
            r["path"]: [
                d
                for d in r["dependencies"]
                if d.startswith("opsmesh.") and d.split(".")[1] in BUSINESS_ROOTS
            ]
            for r in records
            if r["path"].startswith("shared/")
            and any(
                d.startswith("opsmesh.") and d.split(".")[1] in BUSINESS_ROOTS
                for d in r["dependencies"]
            )
        },
    }
    print(json.dumps(facts, ensure_ascii=False, indent=2))


def check_snapshot() -> None:
    inventory = json.loads((OUTPUT / "source-inventory.json").read_text(encoding="utf-8"))
    expected = {r["path"]: r["sha256"] for r in inventory["files"]}
    current = {
        p.relative_to(APP).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in APP.rglob("*.py")
    }
    if current != expected:
        raise SystemExit(
            "Application source differs from review snapshot; refresh review before delivery."
        )
    print(f"Verified {len(expected)} unchanged source files.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--architecture-check", action="store_true")
    args = parser.parse_args()
    if args.architecture_check:
        check_architecture()
        return
    if args.facts:
        review_facts()
        return
    if args.check:
        check_snapshot()
        return
    graph = grimp.build_graph("opsmesh", cache_dir=None)
    files = [inspect_file(path, graph) for path in sorted(APP.rglob("*.py"))]
    packages = defaultdict(list)
    for record in files:
        packages[str(Path(record["path"]).parent).replace("\\", "/")].append(record)
    cycles = sorted(graph.nominate_cycle_breakers("opsmesh"))
    summary = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": len(files),
        "implementation_files": sum(not f["path"].endswith("__init__.py") for f in files),
        "source_lines": sum(f["lines"] for f in files),
        "directories_below_app": len(packages) - 1,
        "pure_forwarders": [
            f["path"] for f in files if f["forwarder"] and not f["path"].endswith("__init__.py")
        ],
        "graph_modules": len(graph.modules),
        "graph_imports": graph.count_imports(),
        "cycle_break_candidates": cycles,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "source-inventory.json").write_text(
        json.dumps({"summary": summary, "files": files}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    digest = []
    for package, records in packages.items():
        digest.append(f"\n## {package}")
        for record in records:
            if record["path"].endswith("__init__.py"):
                continue
            declarations = []
            for item in record["declarations"]:
                if item["kind"] == "class":
                    methods = ",".join(
                        method["name"]
                        for method in item["methods"]
                        if not method["name"].startswith("_")
                    )
                    declarations.append(f"{item['name']}({','.join(item['bases'])})[{methods}]")
                else:
                    declarations.append(item["name"])
            flags = []
            if record["forwarder"]:
                flags.append("FORWARDER")
            if record["private_imports"]:
                flags.append(f"PRIVATE_IMPORTS={len(record['private_imports'])}")
            commits = [x for x in record["transactions"] if x["call"].endswith(".commit")]
            if commits:
                flags.append(f"COMMITS={len(commits)}")
            line = (
                f"{Path(record['path']).name} | {record['lines']}L "
                f"in={len(record['callers'])} out={len(record['dependencies'])}"
            )
            if flags:
                line += f" {' '.join(flags)}"
            if declarations:
                line += f" | {'; '.join(declarations)}"
            digest.append(line)
    (OUTPUT / "declaration-review.txt").write_text("\n".join(digest) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in summary.items()
                if key not in {"cycle_break_candidates", "pure_forwarders"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
