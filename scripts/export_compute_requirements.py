"""Export the compute image's MCP dependencies from the repository lockfile."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "deploy/images/compute-requirements.txt"


def render() -> str:
    with tempfile.TemporaryDirectory(prefix="opsmesh-compute-") as temporary:
        exported = Path(temporary) / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--only-group",
                "compute",
                "--no-emit-workspace",
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(exported),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        requirements = exported.read_text("utf-8")
    header = "# Generated from uv.lock by scripts/export_compute_requirements.py.\n"
    return header + requirements


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text("utf-8") != expected:
            raise SystemExit("Compute requirements differ from uv.lock; regenerate them")
    else:
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
