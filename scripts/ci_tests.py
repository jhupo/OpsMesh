"""Focused PR validation; the full suite belongs only to release-prepare.yml."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def select_tests(changed: list[str]) -> list[str]:
    tests = {"backend/tests/test_health.py", "backend/tests/test_release_delivery.py"}
    candidates = list(Path("backend/tests").glob("test_*.py"))
    for name in changed:
        path = Path(name)
        if name.startswith(("operator/", "backend/app/admin/updates/")):
            tests.update(
                {
                    "backend/tests/test_operator_security.py",
                    "backend/tests/test_platform_updates.py",
                }
            )
        if name.startswith(("deploy/", "Dockerfile", "docker-compose", ".github/workflows/")):
            tests.add("backend/tests/test_deployment_assets.py")
        if name.startswith("backend/tests/test_") and path.is_file():
            tests.add(path.as_posix())
        if name.startswith("backend/app/"):
            domain = path.parts[2].removesuffix("s")
            for test in candidates:
                if domain in test.stem or path.stem in test.stem:
                    tests.add(test.as_posix())
    return sorted(tests)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    if not args.base or set(args.base) == {"0"}:
        parser.error("A real base commit is required")
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", f"{args.base}...HEAD"], text=True
    ).splitlines()
    tests = select_tests(changed)
    print("Focused test targets:", *tests, flush=True)
    return subprocess.run([sys.executable, "-m", "pytest", *tests], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
