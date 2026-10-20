"""Select current feature tests and their dependent boundaries for pull requests."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDENTS = {
    "identity": {
        "capabilities",
        "resources",
        "orchestration",
        "extensions",
        "channels",
        "platform",
    },
    "capabilities": {"extensions", "orchestration", "channels"},
    "resources": {"extensions", "orchestration", "channels"},
    "platform": {"capabilities", "extensions", "orchestration", "channels"},
    "extensions": {"orchestration", "channels"},
    "orchestration": {"channels"},
    "temporal": {"identity", "extensions", "orchestration", "platform"},
    "telemetry": {"bootstrap", "temporal", "identity", "resources", "platform"},
}
NATIVE_SANDBOX_TEST = "tests/execution/test_sandbox_docker.py"


def select_tests(changed: list[str]) -> list[str]:
    """Return existing targets from the current feature tree."""
    directories: set[str] = set()
    selected: set[str] = set()
    features: set[str] = set()
    for name in changed:
        path = Path(name)
        if name.startswith("tests/") and path.suffix == ".py":
            if path.name == "conftest.py" and len(path.parts) == 2:
                directories.add("tests")
            elif not path.name.startswith("test_") or not (ROOT / path).is_file():
                directories.add(str(path.parent).replace("\\", "/"))
        if (
            path.suffix == ".py"
            and path.name.startswith("test_")
            and (ROOT / path).is_file()
            and name.startswith(("tests/", "sdk/python/tests/", "operator/tests/"))
        ):
            selected.add(path.as_posix())
        if name.startswith("src/opsmesh/"):
            feature = path.parts[2]
            if feature in {"shared", "bootstrap", "config.py", "__init__.py"}:
                directories.add("tests")
            else:
                features.add(feature)
                directories.add("tests/bootstrap")
        if name in {"pyproject.toml", "uv.lock", "scripts/ci_tests.py"} or name.startswith(
            "migrations/"
        ):
            directories.update({"tests", "sdk/python/tests", "operator/tests"})
        if name.startswith("sdk/python/"):
            directories.update({"sdk/python/tests", "tests/bootstrap"})
            features.update({"capabilities", "extensions", "orchestration", "channels"})
        if name.startswith(("operator/", "deploy/", ".github/", "scripts/")):
            directories.update({"operator/tests", "tests/engineering", "tests/platform"})
    pending = list(features)
    while pending:
        for feature in DEPENDENTS.get(pending.pop(), set()) - features:
            features.add(feature)
            pending.append(feature)
    directories.update(f"tests/{feature}" for feature in features)
    if "orchestration" in features:
        directories.add("tests/execution")
    for directory in directories:
        folder = ROOT / directory
        if folder.is_dir():
            selected.update(path.relative_to(ROOT).as_posix() for path in folder.rglob("test_*.py"))
    return sorted(selected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if not args.base or set(args.base) == {"0"}:
        parser.error("A real base commit is required")
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRT", f"{args.base}...HEAD"],
        text=True,
        cwd=ROOT,
    ).splitlines()
    tests = select_tests(changed)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"has_tests={str(bool(tests)).lower()}\n")
            output.write(f"native_sandbox={str(NATIVE_SANDBOX_TEST in tests).lower()}\n")
        print("Focused test targets:", *tests, flush=True)
        return 0
    if not tests:
        print("No affected product flows; static checks cover this change.", flush=True)
        return 0
    print("Focused test targets:", *tests, flush=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *tests], cwd=ROOT, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
