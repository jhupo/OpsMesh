"""Read application source and emit reproducible structural-review evidence only."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import grimp

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend/app"
OUTPUT = ROOT / "docs/reviews/app-layout-2026-09-14"


def expression(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else ""


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
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
        "core_business_dependencies": {
            r["path"]: [
                d
                for d in r["dependencies"]
                if d.startswith(
                    ("backend.app.domains.", "backend.app.runtime.", "backend.app.api.")
                )
            ]
            for r in records
            if r["path"].startswith("core/")
            and any(
                d.startswith(("backend.app.domains.", "backend.app.runtime.", "backend.app.api."))
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
    manifest = OUTPUT / "file-dispositions.csv"
    if manifest.exists():
        with manifest.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != len(expected) or {r["source"]: r["sha256"] for r in rows} != expected:
            raise SystemExit("Disposition manifest does not cover the exact source snapshot.")
    print(
        f"Verified {len(expected)} unchanged source files; "
        "disposition coverage checked when present."
    )


def write_dispositions() -> None:
    inventory = json.loads((OUTPUT / "source-inventory.json").read_text(encoding="utf-8"))
    rules = json.loads((OUTPUT / "disposition-rules.json").read_text(encoding="utf-8"))
    records = {r["path"]: r for r in inventory["files"]}
    decisions = {}
    prefixes = sorted(rules["prefixes"], key=lambda r: len(r["source"]), reverse=True)
    for path in records:
        if path.endswith("__init__.py"):
            continue
        decision = {
            "action": "keep",
            "targets": [path],
            "phase": "R0",
            "reason": (
                "Retain cohesive responsibility; update imports to new owners. "
                "No implementation deletion approved."
            ),
        }
        for rule in prefixes:
            if path.startswith(rule["source"]):
                target = rule["target"] + path[len(rule["source"]) :]
                decision = {
                    "action": "move" if target != path else "keep",
                    "targets": [target],
                    "phase": rule["phase"],
                    "reason": rule["reason"],
                }
                break
        decisions[path] = decision
    used_overrides = set()
    for rule in rules["overrides"]:
        for path in rule["sources"]:
            if path not in decisions or path in used_overrides:
                raise ValueError(f"Invalid or repeated override: {path}")
            used_overrides.add(path)
            decisions[path] = {key: rule[key] for key in ("action", "targets", "phase", "reason")}
    for path, record in records.items():
        if path not in decisions or not path.startswith("api/schemas/") or not record["forwarder"]:
            continue
        owners = sorted(
            {
                i["module"].removeprefix("backend.app.").replace(".", "/") + ".py"
                for i in record["imports"]
                if i["module"].startswith("backend.app.")
            }
        )
        targets = sorted({target for owner in owners for target in decisions[owner]["targets"]})
        decisions[path] = {
            "action": "remove-forwarder",
            "targets": targets,
            "phase": "R2",
            "reason": (
                "Import each symbol from its owning contract; remove forwarding file "
                "only after all callers and OpenAPI checks are updated."
            ),
        }
    target_sources = defaultdict(list)
    for source, decision in decisions.items():
        for target in decision["targets"]:
            target_sources[target].append(source)
    target_dirs = {"."}
    for target in target_sources:
        target_dirs.update(p.as_posix() for p in Path(target).parents)
    for path in records:
        if not path.endswith("__init__.py"):
            continue
        retained = Path(path).parent.as_posix() in target_dirs
        decisions[path] = {
            "action": "retain-package-marker" if retained else "remove-package-marker",
            "targets": [path] if retained else [],
            "phase": "R8",
            "reason": (
                "Keep package marker without compatibility exports; "
                "explicit composition replaces eager handler exports."
            )
            if retained
            else (
                "Remove obsolete package marker after migrations; "
                "do not remove retained child packages or runtime data."
            ),
        }
    fieldnames = [
        "source",
        "sha256",
        "lines",
        "action",
        "targets",
        "phase",
        "reason",
        "declarations",
        "callers",
        "dependencies",
        "private_imports",
        "review_method",
    ]
    with (OUTPUT / "file-dispositions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for path, record in records.items():
            decision = decisions[path]
            writer.writerow(
                {
                    "source": path,
                    "sha256": record["sha256"],
                    "lines": record["lines"],
                    **{k: decision[k] for k in ("action", "phase", "reason")},
                    "targets": ";".join(decision["targets"]),
                    "declarations": ";".join(d["name"] for d in record["declarations"]),
                    "callers": ";".join(record["callers"]),
                    "dependencies": ";".join(record["dependencies"]),
                    "private_imports": ";".join(record["private_imports"]),
                "review_method": (
                    "full-source AST/import inventory; per-file declaration review; "
                    "selective implementation inspection "
                    "(not exhaustive line-by-line verification)"
                ),
                }
            )
    for target in rules["new_files"]:
        target_sources.setdefault(target, [])
        target_dirs.update(p.as_posix() for p in Path(target).parents)
    footprint = {
        "source_files": len(records),
        "actions": dict(Counter(d["action"] for d in decisions.values())),
        "planned_implementation_files": len(target_sources),
        "planned_package_directories": len(target_dirs),
        "new_files_without_single_source": rules["new_files"],
        "target_sources": dict(sorted(target_sources.items())),
        "notes": (
            "Design footprint, not implemented changes or fixed quotas. "
            "Targets of remove-forwarder are callers' replacement imports, "
            "not copy/merge instructions."
        ),
    }
    (OUTPUT / "target-footprint.json").write_text(
        json.dumps(footprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in footprint.items() if k != "target_sources"},
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--manifest", action="store_true")
    args = parser.parse_args()
    if args.facts:
        review_facts()
        return
    if args.check:
        check_snapshot()
        return
    if args.manifest:
        write_dispositions()
        return
    graph = grimp.build_graph("backend", cache_dir=None)
    files = [inspect_file(path, graph) for path in sorted(APP.rglob("*.py"))]
    packages = defaultdict(list)
    for record in files:
        packages[str(Path(record["path"]).parent).replace("\\", "/")].append(record)
    cycles = sorted(graph.nominate_cycle_breakers("backend.app"))
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
            digest.append(
                f"{Path(record['path']).name} | {record['lines']}L "
                f"in={len(record['callers'])} out={len(record['dependencies'])} "
                f"{' '.join(flags)} | {'; '.join(declarations)}"
            )
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
