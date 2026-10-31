"""Generate documentation from tracked-source locations, omitting caches and empty directories."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
DIRECTORIES = {
    ".github",
    "src",
    "tests",
    "scripts",
    "docs",
    "deploy",
    "examples",
    "migrations",
    "web",
}


def children(directory: Path) -> list[Path]:
    result = []
    for path in directory.iterdir():
        if path.name in EXCLUDED or path.suffix == ".pyc":
            continue
        if directory == ROOT and (
            (path.is_dir() and path.name not in DIRECTORIES)
            or (
                path.is_file()
                and path.suffix not in {".md", ".toml", ".lock", ".yaml"}
                and not path.name.startswith(".")
                and path.name != "LICENSE"
            )
        ):
            continue
        if path.is_dir() and not children(path):
            continue
        result.append(path)
    return sorted(result, key=lambda item: (not item.is_dir(), item.name.lower()))


def tree(directory: Path, prefix: str = "") -> list[str]:
    lines = []
    entries = children(directory)
    for index, path in enumerate(entries):
        last = index == len(entries) - 1
        lines.append(
            f"{prefix}{'└── ' if last else '├── '}{path.name}{'/' if path.is_dir() else ''}"
        )
        if path.is_dir():
            lines.extend(tree(path, prefix + ("    " if last else "│   ")))
    return lines


if __name__ == "__main__":
    count = len(list((ROOT / "src").rglob("*.py")))
    content = (
        "# opsmesh 实际源码与工程文件树\n\n"
        f"当前 {count} 个Python源码文件。由`scripts/generate_source_tree.py`生成。"
        "排除缓存、构建产物和没有文件的目录。不把预留名称当作已实现功能。\n\n"
        "```text\nopsmesh/\n" + "\n".join(tree(ROOT)) + "\n```\n"
    )
    (ROOT / "docs/reference/source-tree.md").write_text(content, encoding="utf-8")
    print(f"Generated source tree for {count} Python files")
