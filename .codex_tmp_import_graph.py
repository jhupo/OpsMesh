from pathlib import Path

root = Path("backend/app")
pkg = "backend.app.domains.orchestration.tasks."
task_dir = root / "domains/orchestration/tasks"
all_text = [(p, p.read_text(encoding="utf-8")) for p in root.rglob("*.py") if "__pycache__" not in p.parts]
for source in sorted(task_dir.glob("*.py")):
    if source.name == "__init__.py":
        continue
    stem = source.stem
    needle = pkg + stem
    users = [p.relative_to(root).as_posix() for p, text in all_text if p != source and needle in text]
    print(f"{stem:35} {', '.join(users) if users else '-'}")
